@echo off
chcp 65001 > nul
title 고시 모니터 서버

echo.
echo ====================================
echo   고시 통합 분석 시스템 서버 시작
echo ====================================
echo.

:: Python 설치 확인
python --version > nul 2>&1
if errorlevel 1 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo   https://www.python.org 에서 Python 3.10 이상을 설치하세요.
    pause
    exit /b 1
)

:: 현재 폴더를 스크립트 위치로 이동
cd /d "%~dp0"

:: 패키지 설치
echo [1/2] 패키지 설치 중...
pip install -r requirements.txt -q
if errorlevel 1 (
    echo [오류] 패키지 설치 실패. 오류 내용을 확인하세요.
    pause
    exit /b 1
)

:: 서버 실행
echo [2/2] 서버 시작 중...
echo.
echo 브라우저에서 아래 주소로 접속하세요:
echo   ^> http://localhost:5001
echo.
echo 서버를 종료하려면 이 창을 닫거나 Ctrl+C 를 누르세요.
echo.

python main.py serve

pause
