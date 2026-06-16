from __future__ import annotations

import json
import argparse
import queue
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "frontend"
DATA_DIR = ROOT / "data"
STATE_FILE = DATA_DIR / "state.json"
PRINT_LOG_FILE = DATA_DIR / "print-jobs.jsonl"
FIREFOX_PROFILE_DIR = DATA_DIR / "managed-firefox-profile"
TEMP_DIR = DATA_DIR / "tmp"
REMOTE_INSPECT_DIR = ROOT / ".remote-inspect"
TZ = timezone(timedelta(hours=8))

if REMOTE_INSPECT_DIR.exists():
    sys.path.insert(0, str(REMOTE_INSPECT_DIR))

try:
    import danmu_cli as douyin_fetcher_module
    from danmu_cli import ChatMessage, DanmuFetcher

    _original_generate_signature = douyin_fetcher_module.generate_signature

    def _generate_signature_with_project_assets(wss: str, script_file: str = "sign.js") -> str:
        return _original_generate_signature(wss, str(REMOTE_INSPECT_DIR / "sign.js"))

    douyin_fetcher_module.generate_signature = _generate_signature_with_project_assets
except Exception:
    ChatMessage = None
    DanmuFetcher = None
    douyin_fetcher_module = None


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def next_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"


def firefox_candidates() -> list[Path]:
    return [
        Path(r"D:\custom_app\firefox\firefox.exe"),
        Path(r"C:\Program Files\Mozilla Firefox\firefox.exe"),
        Path(r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe"),
    ]


def find_firefox_executable() -> Path | None:
    for candidate in firefox_candidates():
        if candidate.exists():
            return candidate
    return shutil.which("firefox") and Path(str(shutil.which("firefox")))


@dataclass
class AppState:
    rooms: list[dict[str, Any]] = field(default_factory=list)
    danmu: list[dict[str, Any]] = field(default_factory=list)
    winners: list[dict[str, Any]] = field(default_factory=list)
    print_jobs: list[dict[str, Any]] = field(default_factory=list)
    settings: dict[str, Any] = field(
        default_factory=lambda: {
            "running": False,
            "serialMode": "flow",
            "serialStart": 1,
            "serialEnd": 999999,
            "includeDecimal": True,
            "formatRules": ["pureNumber"],
            "keywords": [],
            "limitEnabled": True,
            "limitCount": 100,
            "fastPassEnabled": False,
            "fastPassSeconds": 30,
            "dedupeEnabled": True,
            "dedupeSeconds": 5,
            "lampPriority": False,
            "emptyPrintEnabled": False,
            "template": "标签纸60x40",
            "printer": "HPRT N31D",
            "captureSource": "studio",
        }
    )
    subscribers: list[queue.Queue] = field(default_factory=list)
    auth_sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    capture_workers: dict[str, Any] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)


STATE = AppState()


NAMES = ["小鹿", "花花", "阿泽", "南风", "糖糖", "米粒", "青柠", "一只橙", "星河", "泡芙"]
CHAT_TEMPLATES = [
    "拍{num}",
    "我要{num}",
    "{num} 号",
    "抽我 {num}",
    "来了来了",
    "主播看看我",
    "拍 {num} 谢谢",
    "中奖关键词 {num}",
]


def is_valid_studio_control_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    query = (parsed.query or "").lower()
    if "jinritemai.com" not in host:
        return False
    return "/live/control" in path or "live/control" in query


def room_is_bound(room: dict[str, Any]) -> bool:
    source_type = room.get("sourceType") or "web"
    if source_type == "studio":
        return room.get("captureStatus") == "已绑定"
    return True


def sanitize_loaded_data(data: dict[str, Any]) -> dict[str, Any]:
    rooms = data.get("rooms", [])
    valid_rooms: list[dict[str, Any]] = []
    valid_room_ids: set[str] = set()
    seen_room_keys: set[str] = set()
    for room in rooms:
        if room.get("name") == "示例直播间" and room.get("url") == "https://live.douyin.com/745964462470":
            continue
        if room.get("sourceType") == "studio" and not room_is_bound(room):
            continue
        room_key = f"{room.get('sourceType') or 'web'}::{room.get('controlUrl') or room.get('url') or room.get('id')}"
        if room_key in seen_room_keys:
            continue
        seen_room_keys.add(room_key)
        valid_rooms.append(room)
        if room.get("id"):
            valid_room_ids.add(room["id"])

    danmu = [item for item in data.get("danmu", []) if item.get("roomId") in valid_room_ids]
    winners = [item for item in data.get("winners", []) if not item.get("roomName") or item.get("roomName") in {room.get("name") for room in valid_rooms}]
    return {
        **data,
        "rooms": valid_rooms,
        "danmu": danmu,
        "winners": winners,
    }


def load_state() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if not STATE_FILE.exists():
        return
    with STATE_FILE.open("r", encoding="utf-8") as fp:
        data = sanitize_loaded_data(json.load(fp))
    with STATE.lock:
        STATE.rooms = data.get("rooms", [])
        STATE.danmu = data.get("danmu", [])[-500:]
        STATE.winners = data.get("winners", [])
        STATE.print_jobs = data.get("printJobs", [])
        STATE.settings.update(data.get("settings", {}))
        STATE.settings["running"] = False


def save_state() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with STATE.lock:
        payload = {
            "rooms": STATE.rooms,
            "danmu": STATE.danmu[-500:],
            "winners": STATE.winners[-200:],
            "printJobs": STATE.print_jobs[-200:],
            "settings": {**STATE.settings, "running": False},
        }
    with STATE_FILE.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


def publish(event: str, payload: Any) -> None:
    message = {"event": event, "payload": payload}
    stale: list[queue.Queue] = []
    with STATE.lock:
        for subscriber in STATE.subscribers:
            try:
                subscriber.put_nowait(message)
            except queue.Full:
                stale.append(subscriber)
        for subscriber in stale:
            STATE.subscribers.remove(subscriber)


def normalize_room_url(url: str) -> str:
    url = url.strip()
    if not url:
        return ""
    if url.startswith("http"):
        return url
    return f"https://live.douyin.com/{url}"


def get_studio_auth_url() -> str:
    return control_center_url()


def control_center_url() -> str:
    return "https://fxg.jinritemai.com/ffa/content-tool/live/control"


def homepage_url() -> str:
    return "https://fxg.jinritemai.com/ffa/mshop/homepage/index"


def session_profile_dir(session_id: str) -> Path:
    return FIREFOX_PROFILE_DIR / session_id


def session_cookie_db(profile_dir: Path) -> Path:
    return profile_dir / "cookies.sqlite"


def launch_managed_firefox(session_id: str) -> dict[str, Any]:
    firefox_path = find_firefox_executable()
    if not firefox_path:
        raise ValueError("未找到 Firefox 可执行文件")
    profile_dir = session_profile_dir(session_id)
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        str(firefox_path),
        "-no-remote",
        "-new-instance",
        "-profile",
        str(profile_dir),
        control_center_url(),
    ]
    process = subprocess.Popen(args)
    return {
        "firefoxPath": str(firefox_path),
        "profileDir": str(profile_dir),
        "pid": process.pid,
    }


def read_firefox_cookies(profile_dir: Path) -> dict[str, str]:
    cookie_db = session_cookie_db(profile_dir)
    if not cookie_db.exists():
        return {}
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    temp_db = TEMP_DIR / f"{cookie_db.stem}-{int(time.time() * 1000)}.sqlite"
    shutil.copy2(cookie_db, temp_db)
    try:
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT name, value, host
            FROM moz_cookies
            WHERE host LIKE '%jinritemai.com%' OR host LIKE '%douyin.com%'
            """
        )
        rows = cursor.fetchall()
        conn.close()
    finally:
        if temp_db.exists():
            temp_db.unlink(missing_ok=True)
    return {name: value for name, value, _host in rows}


def fetch_with_cookies(url: str, cookies: dict[str, str]) -> str:
    response = requests.get(
        url,
        cookies=cookies,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
            )
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.text


def parse_shop_name(page_text: str) -> str:
    title_match = re.search(r"<title>(.*?)</title>", page_text, re.IGNORECASE | re.DOTALL)
    if title_match:
        raw_title = unescape(title_match.group(1)).strip()
        cleaned_title = re.sub(r"\s*[-|_].*$", "", raw_title).strip()
        if cleaned_title and cleaned_title not in {"抖店", "直播中控台", "首页"}:
            return cleaned_title

    patterns = [
        r'"shopName"\s*:\s*"([^"]+)"',
        r'"storeName"\s*:\s*"([^"]+)"',
        r'"merchantName"\s*:\s*"([^"]+)"',
        r'"userName"\s*:\s*"([^"]+)"',
        r'"shop_name"\s*:\s*"([^"]+)"',
        r'"mall_name"\s*:\s*"([^"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, page_text)
        if match:
            name = decode_js_text(match.group(1)).strip()
            if name:
                return name
    return ""


def decode_js_text(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except Exception:
        return unescape(value)


def parse_live_ids(*page_texts: str) -> list[str]:
    candidates: list[str] = []
    patterns = [
        r"https://live\.douyin\.com/(\d{6,24})",
        r'"webRid"\s*:\s*"(\d{6,24})"',
        r'"web_rid"\s*:\s*"(\d{6,24})"',
        r'"roomIdStr"\s*:\s*"(\d{6,24})"',
        r'"promoteId"\s*:\s*"(\d{6,24})"',
    ]
    for page_text in page_texts:
        if not page_text:
            continue
        for pattern in patterns:
            for live_id in re.findall(pattern, page_text):
                if live_id not in candidates:
                    candidates.append(live_id)
    return candidates


def append_real_danmu(room: dict[str, Any], comments: list[dict[str, str]]) -> None:
    if not comments:
        return
    with STATE.lock:
        existing_keys = {(item.get("roomId"), item.get("userName"), item.get("content")) for item in STATE.danmu}
    for comment in comments:
        key = (room["id"], comment["userName"], comment["content"])
        if key in existing_keys:
            continue
        item = {
            "id": next_id("dm"),
            "event": "chat",
            "roomId": room["id"],
            "roomName": room["name"],
            "userName": comment["userName"],
            "userId": "",
            "content": comment["content"],
            "matchedContent": extract_match(comment["content"], STATE.settings),
            "batchNo": "",
            "status": "matched" if extract_match(comment["content"], STATE.settings) else "pending",
            "publicTime": now_iso(),
            "createdAt": now_iso(),
        }
        with STATE.lock:
            STATE.danmu.insert(0, item)
            STATE.danmu = STATE.danmu[:500]
        publish("danmu", item)


def close_managed_firefox(pid: int | None) -> None:
    if not pid:
        return
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False, capture_output=True)
    except Exception:
        pass


def stop_capture_worker(room_id: str) -> None:
    with STATE.lock:
        worker = STATE.capture_workers.pop(room_id, None)
    if worker:
        worker.stop()


class RealDanmuWorker:
    def __init__(self, room: dict[str, Any], live_id: str):
        self.room_id = room["id"]
        self.room_name = room["name"]
        self.live_id = live_id
        self.stop_event = threading.Event()
        self.fetcher: Any = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.fetcher:
            try:
                self.fetcher.stop()
            except Exception:
                pass

    def run(self) -> None:
        if not DanmuFetcher or not ChatMessage:
            return
        try:
            self.fetcher = DanmuFetcher(self.live_id, abogus_file=str(REMOTE_INSPECT_DIR / "a_bogus.js"))
            self.fetcher.start(self.on_message)
        except Exception:
            pass

    def on_message(self, message_obj: Any, _server_now_ms: int) -> None:
        if self.stop_event.is_set() or not isinstance(message_obj, ChatMessage):
            return
        content = (message_obj.content or "").strip()
        user_name = (message_obj.user.nick_name or "").strip()
        if not content or not user_name:
            return
        matched_content = extract_match(content, STATE.settings)
        item = {
            "id": next_id("dm"),
            "event": "chat",
            "roomId": self.room_id,
            "roomName": self.room_name,
            "userName": user_name,
            "userId": str(message_obj.user.id or ""),
            "content": content,
            "matchedContent": matched_content,
            "batchNo": "",
            "status": "matched" if matched_content else "pending",
            "publicTime": now_iso(),
            "createdAt": now_iso(),
        }
        with STATE.lock:
            duplicate = any(
                existing.get("roomId") == item["roomId"]
                and existing.get("userName") == item["userName"]
                and existing.get("content") == item["content"]
                for existing in STATE.danmu[:100]
            )
            if duplicate:
                return
            STATE.danmu.insert(0, item)
            STATE.danmu = STATE.danmu[:500]
        publish("danmu", item)


def start_real_danmu_capture(room: dict[str, Any], live_id: str) -> None:
    if not live_id or not DanmuFetcher:
        return
    with STATE.lock:
        existing = STATE.capture_workers.get(room["id"])
        if existing and getattr(existing, "live_id", "") == live_id:
            return
    stop_capture_worker(room["id"])
    worker = RealDanmuWorker(room, live_id)
    with STATE.lock:
        STATE.capture_workers[room["id"]] = worker
    worker.start()


def extract_match(content: str, settings: dict[str, Any]) -> str:
    keywords = [k for k in settings.get("keywords", []) if k]
    if keywords and not any(k in content for k in keywords):
        return ""
    if "pureNumber" in settings.get("formatRules", []):
        pattern = r"\d+(?:\.\d+)?" if settings.get("includeDecimal") else r"\d+"
        match = re.search(pattern, content)
        if not match:
            return ""
        value = match.group(0)
        try:
            numeric = float(value)
            if numeric < float(settings.get("serialStart", 1)):
                return ""
            if numeric > float(settings.get("serialEnd", 999999)):
                return ""
        except ValueError:
            return ""
        return value
    return content if content else ""


def make_danmu(room: dict[str, Any]) -> dict[str, Any]:
    number = random.randint(
        int(STATE.settings.get("serialStart", 1)),
        min(int(STATE.settings.get("serialEnd", 999999)), 999),
    )
    content = random.choice(CHAT_TEMPLATES).format(num=number)
    matched = extract_match(content, STATE.settings)
    return {
        "id": next_id("dm"),
        "event": "chat",
        "roomId": room["id"],
        "roomName": room["name"],
        "userName": random.choice(NAMES),
        "userId": random.randint(10000000, 99999999),
        "content": content,
        "matchedContent": matched,
        "batchNo": "",
        "status": "matched" if matched else "pending",
        "publicTime": now_iso(),
        "createdAt": now_iso(),
    }


def collector_loop() -> None:
    while True:
        time.sleep(random.uniform(1.6, 3.4))
        with STATE.lock:
            running = STATE.settings.get("running", False)
            rooms = [
                room
                for room in STATE.rooms
                if room.get("enabled", True) and room.get("sourceType") == "simulate"
            ]
        if not running or not rooms:
            continue
        room = random.choice(rooms)
        item = make_danmu(room)
        with STATE.lock:
            STATE.danmu.insert(0, item)
            STATE.danmu = STATE.danmu[:500]
        publish("danmu", item)


def dashboard_payload() -> dict[str, Any]:
    with STATE.lock:
        return {
            "rooms": STATE.rooms,
            "danmu": STATE.danmu,
            "winners": STATE.winners,
            "printJobs": STATE.print_jobs,
            "settings": STATE.settings,
            "integrations": {
                "douyinStudio": {
                    "authUrl": get_studio_auth_url(),
                    "controlCenterHint": "登录后进入抖店或直播工作台，再从直播中控台读取弹幕。",
                    "status": "ready",
                    "controlCenterUrl": control_center_url(),
                }
            },
        }


def add_room(payload: dict[str, Any]) -> dict[str, Any]:
    name = (payload.get("name") or "").strip()
    source_type = (payload.get("sourceType") or "studio").strip() or "studio"
    url = normalize_room_url(payload.get("url") or "")
    control_url = ""
    if source_type == "studio":
        control_url = (payload.get("controlUrl") or "").strip()
        if not payload.get("bindingConfirmed"):
            raise ValueError("Please finish login in the control center before binding.")
        if not is_valid_studio_control_url(control_url):
            raise ValueError("Invalid studio control center URL.")
        url = control_url
        with STATE.lock:
            existing = next(
                (
                    room
                    for room in STATE.rooms
                    if room.get("sourceType") == "studio" and room.get("controlUrl") == control_url
                ),
                None,
            )
        if existing:
            room_changed = False
            if name and existing.get("name") in {"Pending Studio", "Pending Studio (Control Center)"}:
                with STATE.lock:
                    existing["name"] = name
                room_changed = True
            live_id = (payload.get("liveId") or "").strip()
            if live_id and existing.get("liveId") != live_id:
                with STATE.lock:
                    existing["liveId"] = live_id
                room_changed = True
            if room_changed:
                save_state()
                publish("room", existing)
            return existing
    elif not url:
        raise ValueError("Room URL is required.")

    with STATE.lock:
        room_index = len(STATE.rooms) + 1
    room = {
        "id": next_id("room"),
        "name": name or ("Pending Studio" if source_type == "studio" else f"Room {room_index}"),
        "url": url,
        "sourceType": source_type,
        "loginUrl": get_studio_auth_url() if source_type == "studio" else "",
        "controlUrl": control_url if source_type == "studio" else "",
        "liveId": (payload.get("liveId") or "").strip(),
        "captureStatus": "bound",
        "boundAt": now_iso(),
        "enabled": True,
        "createdAt": now_iso(),
    }
    with STATE.lock:
        STATE.rooms.append(room)
    save_state()
    publish("room", room)
    return room


def start_studio_auth(payload: dict[str, Any]) -> dict[str, Any]:
    with STATE.lock:
        stale_sessions = list(STATE.auth_sessions.values())
        STATE.auth_sessions.clear()
    for stale_session in stale_sessions:
        close_managed_firefox(stale_session.get("pid"))

    session_id = next_id("auth")
    launch_info = launch_managed_firefox(session_id)
    session = {
        "id": session_id,
        "name": "Pending Studio",
        "status": "waiting_login",
        "detail": "waiting_login",
        "authUrl": get_studio_auth_url(),
        "controlCenterUrl": control_center_url(),
        "profileDir": launch_info["profileDir"],
        "pid": launch_info["pid"],
        "firefoxPath": launch_info["firefoxPath"],
        "createdAt": now_iso(),
    }
    with STATE.lock:
        STATE.auth_sessions[session_id] = session
    publish("auth", session)
    return session


def get_studio_auth_session(session_id: str) -> dict[str, Any]:
    with STATE.lock:
        session = STATE.auth_sessions.get(session_id)
    if not session:
        raise ValueError("登录会话不存在或已过期")
    return session


def complete_studio_auth(payload: dict[str, Any]) -> dict[str, Any]:
    session_id = payload.get("sessionId") or ""
    session = get_studio_auth_session(session_id)
    room = add_room(
        {
            "name": session.get("name") or "Pending Studio",
            "sourceType": "studio",
            "controlUrl": control_center_url(),
            "liveId": session.get("liveId") or "",
            "bindingConfirmed": True,
        }
    )
    with STATE.lock:
        session["status"] = "connected"
        session["detail"] = "connected"
        session["roomId"] = room["id"]
        session["connectedAt"] = now_iso()
        STATE.settings["running"] = True
        settings = dict(STATE.settings)
    save_state()
    close_managed_firefox(session.get("pid"))
    if session.get("liveId"):
        start_real_danmu_capture(room, session["liveId"])
    publish("auth", session)
    publish("settings", settings)
    return {"session": session, "room": room, "settings": settings}


def refresh_auth_sessions() -> None:
    while True:
        time.sleep(2)
        with STATE.lock:
            sessions = list(STATE.auth_sessions.values())
        for session in sessions:
            if session.get("status") != "waiting_login":
                continue
            profile_dir = Path(session.get("profileDir", ""))
            cookies = read_firefox_cookies(profile_dir) if profile_dir else {}
            if not cookies:
                continue
            try:
                homepage_text = fetch_with_cookies(homepage_url(), cookies)
                control_text = fetch_with_cookies(control_center_url(), cookies)
            except Exception:
                continue

            shop_name = parse_shop_name(homepage_text) or parse_shop_name(control_text) or "Pending Studio"
            live_ids = parse_live_ids(homepage_text, control_text)
            with STATE.lock:
                if session.get("status") != "waiting_login":
                    continue
                session["name"] = shop_name
                session["liveId"] = live_ids[0] if live_ids else ""
                session["detail"] = "cookies_detected"
            complete_studio_auth({"sessionId": session["id"]})


def update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    with STATE.lock:
        STATE.settings.update(payload)
        settings = dict(STATE.settings)
    save_state()
    publish("settings", settings)
    return settings


def draw_winner() -> dict[str, Any]:
    with STATE.lock:
        limit = int(STATE.settings.get("limitCount", 100))
        candidates = [item for item in STATE.danmu if item.get("matchedContent")][:limit]
        if STATE.settings.get("dedupeEnabled"):
            won_users = {winner.get("userId") for winner in STATE.winners}
            candidates = [item for item in candidates if item.get("userId") not in won_users]
        if not candidates:
            if not STATE.settings.get("emptyPrintEnabled"):
                raise ValueError("暂无可抽取弹幕")
            winner = {
                "id": next_id("win"),
                "userName": "空奖",
                "userId": "",
                "roomName": "",
                "matchedContent": "",
                "batchNo": build_batch_no(),
                "publicTime": now_iso(),
                "status": "empty",
            }
        else:
            picked = random.choice(candidates)
            winner = {
                "id": next_id("win"),
                "danmuId": picked["id"],
                "userName": picked["userName"],
                "userId": picked["userId"],
                "roomName": picked["roomName"],
                "content": picked["content"],
                "matchedContent": picked["matchedContent"],
                "batchNo": build_batch_no(),
                "publicTime": now_iso(),
                "status": "drawn",
            }
        STATE.winners.insert(0, winner)
    save_state()
    publish("winner", winner)
    create_print_job(winner)
    return winner


def build_batch_no() -> str:
    with STATE.lock:
        count = len(STATE.winners) + 1
    return f"P{datetime.now(TZ).strftime('%m%d')}-{count:04d}"


def create_print_job(winner: dict[str, Any]) -> dict[str, Any]:
    with STATE.lock:
        job = {
            "id": next_id("print"),
            "winnerId": winner["id"],
            "template": STATE.settings.get("template"),
            "printer": STATE.settings.get("printer"),
            "batchNo": winner.get("batchNo"),
            "userName": winner.get("userName"),
            "status": "printed",
            "createdAt": now_iso(),
        }
        STATE.print_jobs.insert(0, job)
    DATA_DIR.mkdir(exist_ok=True)
    with PRINT_LOG_FILE.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(job, ensure_ascii=False) + "\n")
    publish("print", job)
    return job


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "DanmuCatcherMVP/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/state":
            return self.send_json(dashboard_payload())
        if path == "/api/integrations/douyin-studio":
            return self.send_json(
                {
                    "authUrl": get_studio_auth_url(),
                    "controlCenterHint": "登录抖店后进入直播工作台，在直播中控台完成弹幕抓取绑定。",
                    "recommendedName": "抖音直播工作台",
                    "controlCenterUrl": control_center_url(),
                }
            )
        if path.startswith("/api/integrations/douyin-studio/auth/"):
            session_id = path.rsplit("/", 1)[-1]
            return self.send_json(get_studio_auth_session(session_id))
        if path == "/api/danmu":
            return self.handle_danmu_query()
        if path == "/events":
            return self.handle_events()
        return self.serve_static(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/rooms":
                return self.send_json(add_room(payload), HTTPStatus.CREATED)
            if path == "/api/integrations/douyin-studio/auth/start":
                return self.send_json(start_studio_auth(payload), HTTPStatus.CREATED)
            if path == "/api/integrations/douyin-studio/auth/complete":
                return self.send_json(complete_studio_auth(payload))
            if path == "/api/settings":
                return self.send_json(update_settings(payload))
            if path == "/api/capture/start":
                return self.send_json(update_settings({"running": True}))
            if path == "/api/capture/stop":
                return self.send_json(update_settings({"running": False}))
            if path == "/api/draw":
                return self.send_json(draw_winner())
            if path == "/api/print":
                return self.send_json(create_print_job(payload))
            self.send_error_json("接口不存在", HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self.send_error_json(str(exc), HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_error_json(f"服务异常: {exc}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        body = self.rfile.read(length).decode("utf-8")
        return json.loads(body or "{}")

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message: str, status: HTTPStatus) -> None:
        self.send_json({"error": message}, status)

    def handle_danmu_query(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        status = query.get("status", ["all"])[0]
        keyword = query.get("keyword", [""])[0].strip()
        room = query.get("room", ["all"])[0]
        with STATE.lock:
            rows = list(STATE.danmu)
        if status == "matched":
            rows = [row for row in rows if row.get("matchedContent")]
        elif status == "pending":
            rows = [row for row in rows if not row.get("matchedContent")]
        if keyword:
            rows = [row for row in rows if keyword in row.get("userName", "") or keyword in row.get("content", "")]
        if room != "all":
            rows = [row for row in rows if row.get("roomId") == room]
        self.send_json(rows)

    def handle_events(self) -> None:
        subscriber: queue.Queue = queue.Queue(maxsize=100)
        with STATE.lock:
            STATE.subscribers.append(subscriber)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    message = subscriber.get(timeout=15)
                    data = json.dumps(message["payload"], ensure_ascii=False)
                    packet = f"event: {message['event']}\ndata: {data}\n\n".encode("utf-8")
                except queue.Empty:
                    packet = b": ping\n\n"
                self.wfile.write(packet)
                self.wfile.flush()
        except Exception:
            pass
        finally:
            with STATE.lock:
                if subscriber in STATE.subscribers:
                    STATE.subscribers.remove(subscriber)

    def serve_static(self, path: str) -> None:
        if path == "/":
            path = "/index.html"
        file_path = (FRONTEND_DIR / path.lstrip("/")).resolve()
        if FRONTEND_DIR.resolve() not in file_path.parents and file_path != FRONTEND_DIR.resolve():
            return self.send_error(HTTPStatus.FORBIDDEN)
        if not file_path.exists() or not file_path.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        content_type = "text/plain; charset=utf-8"
        if file_path.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif file_path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif file_path.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Danmu Catcher MVP server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()

    load_state()
    threading.Thread(target=collector_loop, daemon=True).start()
    threading.Thread(target=refresh_auth_sessions, daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    print(f"Danmu Catcher MVP running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with STATE.lock:
            worker_ids = list(STATE.capture_workers.keys())
        for room_id in worker_ids:
            stop_capture_worker(room_id)
        with STATE.lock:
            STATE.settings["running"] = False
        save_state()
        server.server_close()


if __name__ == "__main__":
    main()
