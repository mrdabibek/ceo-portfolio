@echo off
title Deploy Usegravity Bot to Cloudflare Workers
echo ========================================================
echo   ◈ USEGRAVITY BOT — CLOUDFLARE WORKERS DEPLOYMENT
echo ========================================================
echo.
echo Cloudflare Workers-ga yuklanmoqda...
call npx wrangler deploy
echo.
echo ========================================================
echo   ✓ YUKLASH TUGADI!
echo ========================================================
echo.
echo Endi Telegram boti 24/7 kompyuteringiz o'chiq bo'lsa ham
echo Cloudflare serverless bulutida uzluksiz ishlaydi!
echo.
pause
