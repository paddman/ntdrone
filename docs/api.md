# API Overview

รายละเอียด Schema แบบ Interactive อยู่ที่ `/docs`

## Authentication

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/auth/login` | ตรวจ Password และคืน 2FA challenge |
| POST | `/api/v1/auth/2fa/verify` | ตรวจ TOTP และออก Session Cookie |
| POST | `/api/v1/auth/logout` | ลบ Session |
| GET | `/api/v1/auth/me` | ข้อมูลผู้ใช้ปัจจุบัน |

`/api/v1/auth/2fa/verify` ต้องส่ง Header:

```http
X-Challenge-Token: <token-from-login>
```

## Team

| Method | Path | Role |
|---|---|---|
| GET | `/api/v1/teams/current` | Team User |
| GET | `/api/v1/teams/current/members` | Team User |
| POST | `/api/v1/teams/current/members` | Team Leader |
| POST | `/api/v1/teams/current/feedback` | Team User |

## Booking

| Method | Path | Role |
|---|---|---|
| GET | `/api/v1/slots` | Authenticated |
| GET | `/api/v1/bookings` | Authenticated |
| POST | `/api/v1/bookings` | Team Leader |
| POST | `/api/v1/bookings/{id}/cancel` | Owner Leader/Admin |

## VPN

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/vpn/status` | Provisioning และ Enabled state |
| GET | `/api/v1/vpn/config` | Download WireGuard config |

## Simulation

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/simulations` | รายการ Run |
| POST | `/api/v1/simulations` | สร้าง Standard/Blind Run |
| POST | `/api/v1/simulations/{id}/start` | Start ใน Active Slot |
| POST | `/api/v1/simulations/{id}/stop` | Stop |
| POST | `/api/v1/simulations/{id}/cancel` | Cancel |
| POST | `/api/v1/simulator/queue/next` | Remote SIM ดึง Blind Queue |
| POST | `/api/v1/simulator/runs/{id}/result` | Remote SIM ส่งผลกลับ |

สอง endpoint ของ Simulator ใช้:

```http
Authorization: Bearer <SIMULATOR_API_TOKEN>
```

## Admin

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/admin/teams` | รายการทีม |
| POST | `/api/v1/admin/teams/{id}/decision` | Approve/Reject |
| POST | `/api/v1/admin/slots` | สร้าง Slot |
| PATCH | `/api/v1/admin/slots/{id}/open` | เปิด/ปิดรับ Booking |
| PATCH | `/api/v1/admin/bookings/{id}/queue` | ปรับ Queue |
