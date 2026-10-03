@echo off
title GitHub Push — 74 Projects Portfolio
echo ========================================================
echo   ◈ PUSHING 74 PROJECTS TO GITHUB: mrdabibek/ceo-portfolio
echo ========================================================
echo.
git push -u origin main
if %errorlevel% equ 0 (
    echo.
    echo ========================================================
    echo   ✓ 74 TA LOYIHA GITHUB GA MUVAFFAQIYATLI YUKLANDI!
    echo   Havola: https://github.com/mrdabibek/ceo-portfolio
    echo ========================================================
) else (
    echo.
    echo [!] Xatolik yuz berdi. Iltimos, GitHub da "ceo-portfolio" nomli
    echo     repository yaratilganligini tekshiring:
    echo     https://github.com/new
)
echo.
pause
