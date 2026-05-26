#!/usr/bin/env python3
"""
검증된 US 종목을 quant_nexus_v20.py의 us_sectors에 자동 병합.
+ US_NAMES에 yfinance longName 기반 한글명 추가.
"""
import ast, json, re, sys, time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "quant_nexus_v20.py"
VALIDATED = Path(__file__).resolve().parent / "us_expansion_validated.json"

# ── 1. 검증 결과 로드 ──
with open(VALIDATED, encoding="utf-8") as f:
    data = json.load(f)
valid_additions = data["valid"]  # {sub_sector: [tickers]}

# ── 2. 원본 파일 로드 ──
src = SRC.read_text(encoding="utf-8")

# ── 3. us_sectors 블록 파싱 ──
us_start_marker = "self.us_sectors = {"
us_start = src.index(us_start_marker)
depth = 0
for i, ch in enumerate(src[us_start + len(us_start_marker):], us_start + len(us_start_marker)):
    if ch == "{": depth += 1
    elif ch == "}": depth -= 1
    if depth == -1:
        us_end = i + 1
        break

us_block = src[us_start + len("self.us_sectors = "):us_end]
us_sectors = ast.literal_eval(us_block)

# ── 4. 서브섹터 → 상위섹터 매핑 (신규 서브섹터 처리) ──
sub_to_parent = {}
for parent, subs in us_sectors.items():
    for sub_name in subs:
        sub_to_parent[sub_name] = parent

# 신규 서브섹터 매핑
NEW_SUBS = {
    "Oil Services": ("Energy", "Oil Services"),
}

# ── 5. 병합 ──
added_count = 0
new_sub_additions = {}  # parent -> {sub: tickers} for brand new sub-sectors

for sub_name, new_tickers in valid_additions.items():
    if sub_name in sub_to_parent:
        parent = sub_to_parent[sub_name]
        existing = set(us_sectors[parent][sub_name])
        merged = sorted(existing | set(new_tickers))
        n = len(merged) - len(existing)
        if n > 0:
            us_sectors[parent][sub_name] = merged
            added_count += n
    elif sub_name in NEW_SUBS:
        parent_name, real_sub = NEW_SUBS[sub_name]
        for p_key in us_sectors:
            if parent_name in p_key:
                us_sectors[p_key][real_sub] = sorted(new_tickers)
                added_count += len(new_tickers)
                break
    else:
        # Try to find parent by fuzzy match
        found = False
        for parent, subs in us_sectors.items():
            for existing_sub in subs:
                if sub_name.lower() in existing_sub.lower() or existing_sub.lower() in sub_name.lower():
                    existing = set(us_sectors[parent][existing_sub])
                    merged = sorted(existing | set(new_tickers))
                    n = len(merged) - len(existing)
                    if n > 0:
                        us_sectors[parent][existing_sub] = merged
                        added_count += n
                    found = True
                    break
            if found:
                break
        if not found:
            print(f"  [WARN] Unmatched sub-sector: {sub_name} ({len(new_tickers)} tickers)")

print(f"[INFO] Merged {added_count} new tickers into us_sectors")

# ── 6. 새 us_sectors 블록 생성 (주석 포함) ──
SECTOR_COMMENTS = {
    "AI & Mega Tech": "1. AI & 빅테크",
    "AI Semiconductors": "2. AI 반도체",
    "Finance & Fintech": "3. 핀테크 & 금융",
    "Industrial & Defense": "4. 산업 & 방산",
    "Energy": "5. 에너지",
    "Healthcare & Biotech": "6. 헬스케어 & 바이오",
    "Consumer & Retail": "7. 소비재 & 리테일",
    "Consumer Staples": "8. 소비자 필수재 & 식음료",
    "Media & Entertainment": "9. 미디어 & 엔터테인먼트",
    "Real Estate": "10. 부동산",
    "Materials & Commodities": "11. 소재 & 원자재",
    "Telecom & 5G": "12. 통신 & 5G",
    "Business & Data Services": "13. 비즈니스 서비스 & 데이터",
}

def format_ticker_list(tickers, indent=38):
    """Format ticker list with line wrapping at ~100 chars."""
    if not tickers:
        return "[]"
    items = [f'"{t}"' for t in tickers]
    lines = []
    current = "["
    for item in items:
        if current == "[":
            current += item
        elif len(current) + len(item) + 1 > 95:
            lines.append(current + ",")
            current = " " * indent + item
        else:
            current += "," + item
    current += "]"
    lines.append(current)
    return "\n".join(lines)

lines = []
lines.append("{")
for idx, (sector_key, subs) in enumerate(us_sectors.items()):
    # Extract emoji-free name for comment
    clean = re.sub(r'^[^\w]+\s*', '', sector_key).strip()
    comment = SECTOR_COMMENTS.get(clean, clean)
    lines.append(f"")
    lines.append(f'            # ── {comment} {"─" * max(1, 58 - len(comment))}')
    lines.append(f'            "{sector_key}": {{')
    for sub_idx, (sub_name, tickers) in enumerate(subs.items()):
        tlist = format_ticker_list(sorted(tickers))
        comma = "," if sub_idx < len(subs) - 1 else ""
        # Pad sub_name to align
        padded = f'"{sub_name}":'
        padded = padded.ljust(24)
        lines.append(f'                {padded}{tlist}{comma}')
    comma = "," if idx < len(us_sectors) - 1 else ""
    lines.append(f'            }}{comma}')

# Close outer dict - no trailing content
lines.append(f'        }}')

new_block = "\n".join(lines)

# ── 7. 파일에 쓰기 ──
new_src = src[:us_start + len("self.us_sectors = ")] + new_block + src[us_end:]

# ── 8. US_NAMES 추가 (yfinance에서 영문명 가져와서 한글화) ──
# 기존 US_NAMES 파싱
names_marker = "US_NAMES: dict[str, str] = {"
names_start = new_src.index(names_marker)
depth = 0
for i, ch in enumerate(new_src[names_start + len(names_marker):], names_start + len(names_marker)):
    if ch == "{": depth += 1
    elif ch == "}": depth -= 1
    if depth == -1:
        names_end = i + 1
        break

names_block = new_src[names_start + len("US_NAMES: dict[str, str] = "):names_end]
existing_names = ast.literal_eval(names_block)

# 모든 신규 티커 중 US_NAMES에 없는 것 찾기
all_new = set()
for tickers in valid_additions.values():
    all_new.update(tickers)
missing_names = sorted(all_new - set(existing_names.keys()))

if missing_names:
    print(f"[INFO] Fetching names for {len(missing_names)} new tickers...")
    try:
        import yfinance as yf
        BATCH = 20
        new_names = {}
        for i in range(0, len(missing_names), BATCH):
            batch = missing_names[i:i+BATCH]
            for ticker in batch:
                try:
                    t = yf.Ticker(ticker)
                    info = t.info or {}
                    name = info.get("longName") or info.get("shortName") or ticker
                    # Truncate long names
                    if len(name) > 30:
                        name = name[:28] + ".."
                    new_names[ticker] = name
                except Exception:
                    new_names[ticker] = ticker
            pct = min(100, (i + BATCH) / len(missing_names) * 100)
            print(f"  ... {pct:.0f}% ({len(new_names)}/{len(missing_names)})")
            time.sleep(0.3)

        # Append to US_NAMES
        if new_names:
            # Find the closing } of US_NAMES
            insert_pos = new_src.rindex("}", names_start, names_end)
            name_entries = []
            for t, n in sorted(new_names.items()):
                name_entries.append(f'        "{t}": "{n}"')
            insert_str = ",\n" + ",\n".join(name_entries)
            new_src = new_src[:insert_pos] + insert_str + new_src[insert_pos:]
            print(f"[INFO] Added {len(new_names)} entries to US_NAMES")
    except ImportError:
        print("[WARN] yfinance not available, skipping US_NAMES update")

# ── 9. 저장 ──
SRC.write_text(new_src, encoding="utf-8")

# 검증
final_src = SRC.read_text(encoding="utf-8")
start2 = final_src.index("self.us_sectors = {")
depth = 0
for i, ch in enumerate(final_src[start2 + len("self.us_sectors = "):], start2 + len("self.us_sectors = ")):
    if ch == "{": depth += 1
    elif ch == "}": depth -= 1
    if depth == 0:
        end2 = i + 1
        break
final_sectors = ast.literal_eval(final_src[start2 + len("self.us_sectors = "):end2])
total = sum(len(v) for subs in final_sectors.values() for v in subs.values())
print(f"\n[DONE] us_sectors: {len(final_sectors)} sectors, {total} total ticker slots")
for name, subs in final_sectors.items():
    counts = ", ".join(f"{k}({len(v)})" for k, v in subs.items())
    print(f"  {name}: {counts}")
