@echo off
title Cloudflare Pages Deployment
echo ========================================================
echo   ◈ CEO PORTFOLIO — CLOUDFLARE PAGES DEPLOYMENT
echo ========================================================
echo.
echo 1-Qadam: Cloudflare tizimiga kirish tekshirilmoqda...
call npx wrangler whoami
if %errorlevel% neq 0 (
    echo.
    echo Cloudflare tizimiga kirmagansiz. Brauzer ochilmoqda...
    echo Iltimos, ochilgan brauzer sahifasida "Authorize" tugmasini bosing!
    call npx wrangler login
)

echo.
echo ========================================================
echo 2-Qadam: Portfolio Cloudflare Pages-ga yuklanmoqda...
echo ========================================================
echo.
call npx wrangler pages deploy . --project-name=ceo-portfolio --commit-dirty=true

echo.
echo ========================================================
echo   ✓ YUKLASH MUVAFFAQIYATLI YAKUNLANDI!
echo ========================================================
pause
