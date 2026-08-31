# Simulation Data Contract v1

Concept Deck ระบุว่าต้องล็อก “ข้อมูลที่ SIM ส่งกลับ Dashboard/DB” แต่ไม่ได้กำหนด Schema ราย field เอกสารนี้จึงเป็น **Contract ที่เสนอและถูกใช้ใน MVP** ไม่ใช่การถอด Schema เดิมจาก Simulator

## Create Run

```json
{
  "booking_id": "uuid",
  "mode": "STANDARD",
  "scenario_name": "waypoint-navigation",
  "config": {
    "mission": "waypoint-navigation",
    "max_altitude_m": 120,
    "timeout_seconds": 900
  }
}
```

`mode` รองรับ `STANDARD` และ `BLIND`

## Push Contract: Portal → Simulator

`POST {SIMULATOR_API_URL}/runs`

```json
{
  "run_id": "uuid",
  "team_id": "uuid",
  "booking_id": "uuid",
  "mode": "BLIND",
  "scenario_name": "waypoint-navigation",
  "config": {},
  "callback_url": "https://portal/api/v1/simulator/runs/<run_id>/result"
}
```

Header:

```http
Authorization: Bearer <SIMULATOR_API_TOKEN>
Content-Type: application/json
```

Expected response:

```json
{
  "run_id": "remote-run-id"
}
```

## Pull Contract: Simulator Queue

`POST /api/v1/simulator/queue/next`

เมื่อมีงาน:

```json
{
  "status": "success",
  "process": "exe",
  "run_id": "uuid",
  "team_id": "uuid",
  "booking_id": "uuid",
  "scenario_name": "waypoint-navigation",
  "config": {}
}
```

เมื่อไม่มีงาน:

```json
{
  "status": "success",
  "process": "end",
  "run_id": null,
  "team_id": null,
  "booking_id": null,
  "scenario_name": null,
  "config": null
}
```

Queue เรียงตาม `created_at` เก่าที่สุด และใช้ `team_id` เป็น tie-breaker เพื่อให้ผลลัพธ์ deterministic

## Result Callback

`POST /api/v1/simulator/runs/{run_id}/result`

```json
{
  "status": "COMPLETED",
  "remote_run_id": "sim-2026-0001",
  "metrics": {
    "completion_percent": 100,
    "altitude_m": 68.4,
    "speed_mps": 9.7,
    "distance_m": 1240.0,
    "telemetry_points": 4820
  },
  "result": {
    "score": 87,
    "summary": "Mission completed",
    "violations": [],
    "artifacts": [
      "telemetry.json",
      "flight.mp4"
    ]
  },
  "output_path": "s3://ntdrone-output/<team_id>/<run_id>/"
}
```

สถานะที่รับได้:

- `RUNNING`
- `COMPLETED`
- `FAILED`
- `STOPPED`
- `CANCELLED`

## Telemetry สำหรับระบบจริง

ไม่ควรส่ง Telemetry ความถี่สูงทุก sample เข้า REST Callback เดียว แนะนำให้:

- ส่ง Live Telemetry ผ่าน WebSocket/MQTT/Kafka
- เก็บ Raw Telemetry เป็น Object Storage
- Callback ส่งเฉพาะ Aggregated Metrics, Result และ Artifact URI
- ทุก Event ต้องมี `run_id`, `team_id`, timestamp UTC และ schema version
