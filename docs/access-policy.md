# Access Policy v1

## Role

### `TEAM_LEADER`

- จัดการข้อมูลทีม
- เพิ่มสมาชิกจนรวมไม่เกิน 4 คน
- จองและยกเลิก Slot
- สร้าง Simulation Config
- Start/Stop/Cancel Simulation ของทีม
- ดาวน์โหลด VPN Config

### `MEMBER`

- Login ด้วย 2FA
- ดู Team, Booking, VPN และ Simulation ของทีม
- ดาวน์โหลด VPN Config
- ส่ง Feedback

### `ADMIN`

- ตรวจเอกสารและ Approve/Reject ทีม
- สร้าง/เปิด/ปิด Slot
- ดู Booking Queue และ Simulation Status
- ดู Feedback และ Audit Log
- ใช้ Swagger/API สำหรับงาน Integration

## Authentication

- Password hash ด้วย Argon2id
- ทุก Login ต้องผ่าน TOTP 2FA
- Session เป็น signed token ใน `HttpOnly`, `SameSite=Strict` cookie
- Secret ของ TOTP และ VPN private key ถูกเข้ารหัสด้วย key ที่ derive จาก `SECRET_KEY`
- Production ต้องเก็บ `SECRET_KEY` ใน Secrets Manager และมีขั้นตอน rotation

## VPN Access

1. Team ต้องได้รับอนุมัติ
2. ระบบสร้าง Key รายทีมและจัดสรร Client IP
3. VPN เริ่มต้น `DISABLED`
4. Worker ตรวจ Booking ในฐานข้อมูลทุก cycle
5. เปิด Peer เมื่อเวลาปัจจุบันอยู่ใน Slot ที่ `CONFIRMED`
6. ปิด Peer อัตโนมัติเมื่อหมดเวลา ถูกยกเลิก ทีมถูก Suspend หรือไม่มี Active Booking
7. `AllowedIPs` ฝั่ง Client จำกัดไว้ที่ Internal LAN `192.168.248.0/24`

Concept เดิมระบุให้ส่ง VPN Key ทาง E-mail ส่วน MVP เปลี่ยนเป็น **ส่ง E-mail แจ้งเตือนแล้วให้ผู้ใช้ Login เพื่อดาวน์โหลด Config** เพื่อลดการเก็บ Private Key ใน Mailbox การเปลี่ยนนี้เป็น Security hardening ที่ตั้งใจทำ ไม่ใช่รายละเอียดจาก Concept เดิม

## Privilege Separation

- API Process ไม่ควรมี `CAP_NET_ADMIN`
- Worker หรือ VPN Controller เท่านั้นที่เรียก WireGuard
- Production ควรแยก VPN Controller เป็น Service บน Network Management Zone
- Script รับเฉพาะ Interface, Public Key และ Allowed IP ห้ามส่ง Shell Command อิสระ

## Default Deny

- บัญชีทีม inactive จนกว่า Admin จะ Approve
- VPN disabled จนมี Active Slot
- Simulator callback ปฏิเสธเมื่อ Bearer Token ไม่ตรง
- Admin endpoint ปฏิเสธทุก Role ที่ไม่ใช่ `ADMIN`
