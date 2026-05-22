# hts_auto.ps1 — HTS 조건검색 자동 입력기
param(
    [string]$Strategy = "value"   # momentum | value | low_vol | earnings | breakout | scalping | all
)

Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Drawing;
using System.Drawing.Imaging;
using System.Windows.Forms;
public class HTSA {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, int x, int y, int d, int e);
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte sc, uint f, int e);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
    public static RECT WinRect(IntPtr h) { RECT r; GetWindowRect(h, out r); return r; }
    public static void Focus(IntPtr h) { SetForegroundWindow(h); System.Threading.Thread.Sleep(300); }
    public static void Move(int x, int y) { SetCursorPos(x, y); System.Threading.Thread.Sleep(60); }
    public static void LClick(int x, int y) {
        Move(x, y);
        mouse_event(2,0,0,0,0); System.Threading.Thread.Sleep(40); mouse_event(4,0,0,0,0);
        System.Threading.Thread.Sleep(120);
    }
    public static void DblClick(int x, int y) {
        LClick(x,y); System.Threading.Thread.Sleep(60); LClick(x,y);
        System.Threading.Thread.Sleep(250);
    }
    public static void ClearAndType(string text) {
        SendKeys.SendWait("^a"); System.Threading.Thread.Sleep(60);
        SendKeys.SendWait("{DEL}"); System.Threading.Thread.Sleep(60);
        foreach (char c in text) { SendKeys.SendWait(c.ToString()); System.Threading.Thread.Sleep(25); }
        System.Threading.Thread.Sleep(400);
    }
    public static void Key(byte vk) {
        keybd_event(vk,0,0,0); System.Threading.Thread.Sleep(40); keybd_event(vk,0,2,0);
        System.Threading.Thread.Sleep(80);
    }
    public static void Cap(string path, int x, int y, int w, int h) {
        var bmp = new Bitmap(w, h);
        using (var g = Graphics.FromImage(bmp)) g.CopyFromScreen(x, y, 0, 0, new Size(w, h));
        bmp.Save(path, ImageFormat.Png);
    }
}
"@ -ReferencedAssemblies System.Windows.Forms,System.Drawing

$HWND  = [IntPtr]266872
$TMP   = $env:TEMP

# ── 창 좌표 계산 ─────────────────────────────────────────────
[HTSA]::Focus($HWND)
$rc    = [HTSA]::WinRect($HWND)
$WL    = $rc.L   # 창 왼쪽 절대 X
$WT    = $rc.T   # 창 상단 절대 Y

# 좌측 패널 내 상대 좌표 (2560px 풀스크린 기준, 좌측 패널 ~285px)
$SEARCH_X = $WL + 142   # 스마트 검색 입력창 X
$SEARCH_Y = $WT + 120   # 스마트 검색 입력창 Y
$TREE_X   = $WL + 150   # 트리 첫 번째 결과 X
$TREE_Y1  = $WT + 165   # 트리 결과 첫 행 Y

# 오른쪽 설정 패널 (조건 추가 후 나타나는 파라미터 영역)
$SET_X    = $WL + 900   # 설정 패널 중앙 X (대략)
$SET_Y    = $WT + 200   # 설정 패널 Y

Write-Host "창 위치: ($WL, $WT), 스마트검색: ($SEARCH_X, $SEARCH_Y)"

# ── 전략별 조건 정의 ───────────────────────────────────────────
# 각 조건: @{검색어; 설정내용 설명 (참고용)}
$strategies = @{

    "scalping" = @(
        # 당일 스캘핑 전략 — 거래량 폭발 + 갭상승 + 모멘텀
        @{ q="거래량 비율 범위";  desc="전일대비 3배 이상" }
        @{ q="갭상승";            desc="1% 이상 갭상승" }
        @{ q="전일대비 범위";     desc="등락률 +2% ~ +15%" }
        @{ q="RSI 범위";          desc="14일 RSI 50 이상 75 이하" }
        @{ q="이동평균 배열";     desc="정배열 (5>20>60)" }
        @{ q="거래대금 범위";     desc="당일 50억 이상" }
        @{ q="주가 범위";         desc="5000원 ~ 200000원" }
        @{ q="볼린저밴드 폭 범위"; desc="Band Width 5 이상 (변동성 있는 종목)" }
    )

    "momentum" = @(
        # 12-1M 가격 모멘텀
        @{ q="ROC 범위";      desc="126일, 20% 이상" }
        @{ q="거래대금 범위"; desc="50억 이상" }
        @{ q="이동평균 배열"; desc="정배열 (5>20>60)" }
        @{ q="ROC 순위";      desc="126일 상위 50종목" }
    )

    "value" = @(
        # 저PBR + 고ROE 가치 전략
        @{ q="주가순자산비율(PBR) 범위"; desc="0.1배 ~ 1.5배" }
        @{ q="자기자본이익률(ROE) 범위"; desc="5% 이상" }
        @{ q="부채비율 범위";            desc="200% 이하" }
        @{ q="영업이익 범위";            desc="0억 초과 (흑자)" }
        @{ q="이동평균 범위";            desc="20일, 괴리율 -5% 이상" }
        @{ q="거래대금 범위";            desc="10억 이상" }
    )

    "low_vol" = @(
        # 저변동성 이상현상
        @{ q="Sigma 순위";        desc="60일 하위 50종목 (변동성 낮은 순)" }
        @{ q="지수 베타계수 범위"; desc="60일봉, 0.3~0.8" }
        @{ q="ROC 범위";          desc="126일, -10% 이상" }
        @{ q="자기자본이익률(ROE) 범위"; desc="0% 이상" }
        @{ q="거래대금 범위";     desc="10억 이상" }
    )

    "earnings" = @(
        # 이익 모멘텀
        @{ q="영업이익 증가율 범위"; desc="10% 이상" }
        @{ q="순이익 증가율 범위";   desc="0% 이상" }
        @{ q="자기자본이익률(ROE) 범위"; desc="8% 이상" }
        @{ q="주가수익비율(PER) 범위(추정)"; desc="0~30배" }
        @{ q="ROC 범위";             desc="63일, 0% 이상" }
        @{ q="거래대금 범위";        desc="10억 이상" }
    )

    "breakout" = @(
        # 기술적 돌파
        @{ q="이동평균 배열";     desc="정배열 (5>20>60)" }
        @{ q="거래량 비율 범위";  desc="전일대비 1.5배 이상" }
        @{ q="52주 신고가";       desc="신고가 -10% 이내" }
        @{ q="MACD 시그널 교차";  desc="골든크로스" }
        @{ q="거래대금 범위";     desc="30억 이상" }
    )
}

$conds = $strategies[$Strategy]
if (-not $conds) {
    Write-Host "알 수 없는 전략: $Strategy"
    exit 1
}

Write-Host "`n[전략: $Strategy] 조건 $($conds.Count)개 추가 시작..."

# ── 공통 필터 먼저 (주가 범위, 거래대금) + 전략 조건들 ─────────
foreach ($cond in $conds) {
    $q    = $cond.q
    $desc = $cond.desc
    Write-Host "  >> 조건 추가: $q ($desc)"

    # 1) 스마트 검색 클릭
    [HTSA]::LClick($SEARCH_X, $SEARCH_Y)
    Start-Sleep -Milliseconds 200

    # 2) 검색어 입력
    [HTSA]::ClearAndType($q)
    Start-Sleep -Milliseconds 600

    # 3) 트리 첫 번째 결과 더블클릭 (조건 추가)
    [HTSA]::DblClick($TREE_X, $TREE_Y1)
    Start-Sleep -Milliseconds 500

    # 스크린샷으로 결과 확인
    $capPath = "$TMP\hts_after_$($q.Replace('/','-').Replace(' ','_').Substring(0, [Math]::Min($q.Length,15))).png"
    [HTSA]::Cap($capPath, $WL, $WT, 1400, 600)
    Write-Host "     캡처: $capPath"
    Start-Sleep -Milliseconds 300
}

Write-Host "`n완료! 설정값은 각 조건 우측 패널에서 수동으로 입력해야 합니다."
Write-Host "캡처 파일들을 확인해 설정 패널 구조를 파악하겠습니다."

# 최종 전체 화면 캡처
[HTSA]::Cap("$TMP\hts_final_$Strategy.png", $WL, $WT, 1400, 800)
Write-Host "최종 캡처: $TMP\hts_final_$Strategy.png"
