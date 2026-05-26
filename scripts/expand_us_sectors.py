#!/usr/bin/env python3
"""
US 섹터 대규모 확충 스크립트
- 러셀 2000 수준 커버리지 목표
- yfinance 교차검증으로 상폐/불량 종목 제거
- 기존 종목 중복 제거
"""
import ast, sys, time, json, os
from pathlib import Path

# ── 1. 기존 종목 추출 ──────────────────────────────────────────────
SRC = Path(__file__).resolve().parent.parent / "quant_nexus_v20.py"
src = SRC.read_text(encoding="utf-8")

start = src.index("self.us_sectors = {")
depth = 0
for i, ch in enumerate(src[start + len("self.us_sectors = "):], start + len("self.us_sectors = ")):
    if ch == "{": depth += 1
    elif ch == "}": depth -= 1
    if depth == 0:
        end = i + 1
        break
us_sectors = ast.literal_eval(src[start + len("self.us_sectors = "):end])
existing = set()
for subs in us_sectors.values():
    for tickers in subs.values():
        existing.update(tickers)
print(f"[INFO] Existing tickers: {len(existing)}")

# ── 2. 후보 종목 (섹터별 ~1500개, 내 지식 기반 러셀1000+2000급) ────
CANDIDATES = {
    # ── AI & Mega Tech ──
    "AI Platform & Cloud": [
        "APPN","ASAN","BSY","CWAN","DT","FROG","JAMF","KVYO","MANH",
        "NCNO","NEWR","SMAR","SQSP","YEXT","ZI","BRZE","ALTR","ENFN",
        "TOST","FSLY","LPSN","VERINT","DOMO","APPF","AYX","AZPN","BL",
        "EVBG","FRSH","CDAY","PAYC","GEN","GWRE","INTA","CARG","SMWB",
    ],
    "AI Infrastructure": [
        "CIEN","PI","EGHT","FFIV","AKAM","VRNT","CALX","INFN","CMBM",
        "MTSI","VIAV","LUMN","UI",
    ],
    "Cybersecurity": [
        "CYBR","QLYS","PRGS","DBX","SCWX","VRNS","SAIL","TELOS",
    ],
    "SaaS & Software": [
        "CCCS","COUP","ESTC","PCOR","TENB","BL","APPF","AZPN","BSY",
        "EVBG","FRSH","LPSN","MANH","SMAR","SQSP","YEXT","CWAN",
    ],
    # ── AI Semiconductors ──
    "Fabless & Analog": [
        "ACMR","ALGM","AOSL","RMBS","SMTC","SYNA","VSH","IPGP","LFUS",
        "POWI","SLAB","DIOD","CRUS","PI","OLED","MXIM","NXPI","SIMO",
    ],
    "Semicon Equipment": [
        "BRKS","CAMT","COHU","FORM","ICHR","KLIC","NVMI","ONTO","UCTT",
        "VECO","ACLS","AEIS","MKSI","ENTG","TER","IPGP","LRCX",
    ],
    "Quantum Computing": [
        "XNDU","INFQ",
    ],
    # ── Finance & Fintech ──
    "Mega Banks & IB": [
        "AX","BOH","BPOP","BXS","CADE","CBSH","CFR","COLB","DCOM",
        "EFSC","FHB","FFIN","FHN","FNB","GBCI","HOMB","HWC","IBOC",
        "NBTB","ONB","OZK","PPBI","SBCF","SFNC","TCBI","TRMK","UBSI",
        "VLY","WBS","WTFC","SSB","UMBF","ABCB","ASB","BANF","BANR",
        "BKU","CATY","CBU","CVBF","FCF","FULT","INDB","LCNB","NWBI",
        "PNFP","PB","SBSI","STBA","TOWN","TMP","WABC","WAFD","WSFS",
    ],
    "Insurance": [
        "AFG","KNSL","LMND","PLMR","RLI","ROOT","SIGI","FAF","FNF",
        "KMPR","ORI","PRA","RNR","STC","THG","WDFC",
    ],
    "Fintech & Payments": [
        "DLO","FLYW","GDOT","MQ","OLO","PAYO","PSFE","RELY","RPAY",
        "EVTC","FLUT","IMXI","NUVEI","SQ","WEX",
    ],
    "Exchanges & Data": [
        "VIRT","MKTX","LSEG","COIN","HOOD","IBKR",
    ],
    "Alt Assets & PE": [
        "STEP","TWO","MAIN","ARCC","FSK","BXSL","HTGC","OBDC","ORCC",
        "TPVG","PSEC","NMFC","SAR","CSWC","GAIN","GLAD","GBDC",
    ],
    # ── Industrial & Defense ──
    "Aerospace & Defense": [
        "ESLT","HXL","TGI","VSEC","DRS","SPR","MOG-A","CRS","AIR",
        "AJRD","DCO","HAYW","POWL",
    ],
    "Power Grid & Infra": [
        "ATKR","BE","FLUX","IEC","MYR","NVT","SPXC","TPC","VST","WATT",
        "XPEL","AMPS","ENPH","GNE","SHLS",
    ],
    "Industrials": [
        "AGCO","AIT","AMRC","B","BCPC","BLD","BLDR","BMI","CBT","CNM",
        "CR","CSL","DCI","DOOR","ENS","ESAB","FBIN","FSS","HEI-A",
        "IEX","KAI","LECO","MIDD","MSA","MTZ","NDSN","NSIT","OSIS",
        "RBC","SITE","SSD","SXI","TKR","TREX","TRN","UFPI","VMI",
        "XYL","ALLE","ALSN","GATX","GGG","GWW","LII","MAS","OTIS",
        "PH","PNR","ROK","ROP","SNA","WAB","WCN","WM","WTS","ASGN",
        "ACA","CNHI","GNRC","GXO","KNX","MATX","PCAR","WSC","SRCL",
        "FLR","APG","CSWI","EBC","FIX","AAON","LNTH",
    ],
    "Transportation": [
        "ARCB","CNI","CP","EXPD","GXO","HUBG","KNX","MATX","RXO",
        "SNDR","WERN","ATSG","HTLD","MRTN","ODFL","SAIA","XPO",
    ],
    "Robotics & Drones": [
        "KTOS","JOBY","ACHR","BKSY","ASTR","PRCT","ONDS",
    ],
    # ── Energy ──
    "Oil & Gas Majors": [
        "CHRD","CPG","ERF","MGY","NOG","OVV","TALO","VTLE","WTI",
        "CIVI","CRGY","MTDR","RRC","SM","AR","EQT",
    ],
    "Midstream & Pipeline": [
        "AM","DTM","HESM","WES","DKL","SMLP","CTRA","TRGP",
    ],
    "Clean Energy": [
        "CSIQ","DQ","JKS","MAXN","SHLS","AMPS","AES","SPWR",
        "FLNC","NOVA","NXT","RUN","ENPH",
    ],
    "Nuclear & Uranium": [
        "URNM","URA","GEV","GLATF","EU",
    ],
    "Utilities": [
        "ALE","AVA","BKH","CWT","HE","IDA","MDU","NWE","NWN","OGE",
        "OGS","OTTR","POR","SJW","SR","SWX","UTL","BEP","CLNE",
    ],
    "Oil Services": [
        "CHX","CLB","DRQ","FTI","HAL","HP","LBRT","NOV","PTEN","RES",
        "TDW","WFRD","WHD","XPRO","PUMP","BOOM","DNOW","MRC","SLCA",
        "WTTR","AROC",
    ],
    # ── Healthcare & Biotech ──
    "Big Pharma": [
        "VTRS","TAK","TEVA","ZTS","CTLT","CRL","PRGO","OGN","ELAN",
        "NBIX","JAZZ","HCM","CORT","SUPN","PCRX","PAHC","ITCI",
    ],
    "GLP-1 & Obesity": [
        "ZEAL","GPCR","TERN","VKTX","CGON","PEPG",
    ],
    "ADC & Gene Therapy": [
        "ACAD","ADMA","AGIO","ALKS","ARWR","BHVN","DVAX","FOLD",
        "GERN","HALO","HRMY","IOVA","MGNX","NUVB","PTCT","RARE",
        "RCKT","RGEN","SAGE","TGTX","UTHR","XERS","ROIV","SMMT",
        "IONS","EXEL","MDGL","AXSM","BBIO","APLS","CRNX","DCPH",
        "KYMR","RCUS","RVMD","ACCD","ACLX","DAWN","MIRM","MYOV",
        "SWTX","TVTX","VRDN","XNCR",
    ],
    "Medical Devices": [
        "AVNS","BRKR","GMED","HAE","IART","ICUI","INMD","MMSI",
        "NARI","NOVT","OFIX","PRCT","TNDM","WRBY","AZEK","ENVX",
        "ESTA","GKOS","INSP","LIVN","MASI","NEOG","NUVA","QDEL",
        "SILK","SWAV","TMDX","VCYT",
    ],
    "Healthcare Services": [
        "ACCD","AMN","BHG","CANO","CHE","ENSG","EVH","HIMS","HZNP",
        "INVA","LHCG","LH","MD","MDRX","OPCH","OSH","PNTG","PRCT",
        "SGRY","SHC","USPH","AMED","CCRN","PINC","AMWL","GDRX",
        "SDGR","TDOC",
    ],
    # ── Consumer & Retail ──
    "E-commerce & Travel": [
        "CARG","CPRT","CVNA","DSKE","FOUR","FWRD","IAC","LYFT","MMYT",
        "OPEN","RVLV","TCOM","TRIP","VTEX","YELP","CARGX",
    ],
    "Retail Giants": [
        "ACI","BOOT","CASY","DECK","FL","GO","GOOS","KTB","LEVI",
        "MNST","ODP","SFM","SMPL","VSCO","WOOF","DBI","PRPL","ROST",
        "TJX","DLTR","DG","BJ","TSCO","WSM","FND","OLLI","BBY",
    ],
    "Restaurants": [
        "ARCO","BJRI","CAKE","DIN","EAT","JACK","NDLS","PLAY","TACO",
        "WEN","LOCO","KRUS","PZZA","FAT","SBUX",
    ],
    "Auto & EV": [
        "BWA","GT","HOG","LCII","SMP","THRM","AUR","CWH","FOXF",
        "GNTX","LEA","VC","APTV","MTOR","PCAR","PATK","ALV","ADNT",
        "GOEV","MULN","NKLA","VLCN","QS",
    ],
    "Luxury & Apparel": [
        "GIII","HBI","LEVI","OXM","SNBR","WOOF","DECK","BIRK","CROX",
        "NKE","ONON","FIGS","WFCF","AEO","ANF","URBN","GOOS","MOV",
    ],
    "Hotels & Gaming": [
        "CHDN","GLPI","GENI","RSI","DKNG","TNL","VAC","WH","WYND",
        "ABNB","BKNG","EXPE","MMYT","PLYA","HGV",
    ],
    "China & EM ADRs": [
        "BEKE","DIDIY","DNUT","EDU","GDS","HUYA","IQ","KC","LKNCY",
        "MOMO","NTES","QFIN","SE","SOHU","TIGR","TCOM","VNET","YMM",
        "YUMC","ATHM","BGNE","HTHT","HCM","RLX","TUYA","WDH","XNET",
    ],
    # ── Consumer Staples ──
    "Beverages & Spirits": [
        "COKE","NBEV","FIZZ","WVVI","SAM","CELH","BF-B","DSGX",
    ],
    "Food & Household": [
        "FRPT","GO","HAIN","IPAR","LW","POST","SMPL","SPB","THS",
        "USFD","VITL","EPC","CALM","JJSF","LNDC","SENEA","UNFI",
        "INGR","DAR","LANC","CHEF","CENTA",
    ],
    "Agriculture & Agri": [
        "ANDE","AVD","CORT","LSB","LMNR","FDP","AGRO","CSAN","DOLE",
    ],
    # ── Media & Entertainment ──
    "Social & Ad Tech": [
        "BMBL","MTCH","ZD","CARG","ANGI","YELP","GENI","MAPS","PERI",
        "MGID","ZETA","APGE","TBLA","OB",
    ],
    "Gaming": [
        "CHDN","GLPI","RSI","AGYS","AGS","BETZ","DKNG","GENI","PENN",
        "PLTK","CZR","LVS","MGM","WYNN","FLUT","GAMB","SRAD",
    ],
    "Streaming & Content": [
        "WBD","PARA","FUBO","COUR","EDR","MSGS","MSGE","LGF-A","LILA",
        "LILAK","TKO","BATRA","BATRK","WWE","ATUS",
    ],
    # ── Real Estate ──
    "Data Center REITs": [
        "QTS","CONE","UNIT","INXN","EQIX","DLR","AMT","CCI",
    ],
    "Industrial REITs": [
        "EGP","COLD","GTY","IIPR","LAND","LTC","NNN","SLG",
        "TRNO","WPT","PSA","CUBE","NSA","LSI",
    ],
    "Residential REITs": [
        "AIRC","NXRT","APLE","IRT","ELME","BRT","NHI","CSR",
    ],
    "Retail & Office": [
        "BRX","CTRE","CUZ","DEI","EPRT","FCPT","HIW","JBGS","KRG",
        "LAMR","LXP","MAC","OFC","PDM","ROIC","SKT","UE","ALEX",
    ],
    "Healthcare REITs": [
        "SBRA","HR","CHCT","GMRE","CTRE","NHI","LTC","PEAK",
    ],
    "Homebuilders": [
        "CCS","GRBK","TMHC","TPH","HOV","WLH","LGIH","DFH","CVCO",
        "MHO","MTH","NVR","PHM","TOL","MDC","BZH","LEGH","SKY",
    ],
    # ── Materials & Commodities ──
    "Chemicals": [
        "AXTA","BCPC","CBT","ECVT","FUL","HUN","IOSP","KOP","KWR",
        "NEU","OEC","RYAM","SCL","WLK","AMRS","GRA","GEVO","LEA",
        "MEOH","TROX","VNTR","WDFC","ASH","AVNT","CC","EMN",
    ],
    "Metals & Mining": [
        "AMR","ATI","CDE","CMP","CRS","EAF","HAYN","HCC","KALU",
        "TMST","TX","VALE","ZEUS","RFP","SID","SWN","ARCH","BTU",
        "CENX","CMC","FCX","RGLD","USAC","WOR","CSTM","MGRC",
    ],
    "Gold & Precious": [
        "AUY","CDE","DRD","EGO","GATO","IAG","MAG","NGD","SSRM",
        "BTG","SA","OR","PAAS","WPM","FNV","NEM","GOLD","GFI",
    ],
    "Lithium & Battery": [
        "LTHM","PLL","ALTM","LIVENT","GNENF","MVST","DCFC","BATT",
    ],
    "Construction Materials": [
        "ROCK","USLM","SUM","ITE","GMS","BECN","IBP","BLDR","FRTA",
    ],
    "Packaging": [
        "BLL","SLGN","BERY","GEF","REYN","AMBP","UFPT","TRS",
    ],
    # ── Telecom ──
    "Telecom Giants": [
        "LBRDA","USM","CNSL","ATUS","WOW","CCOI","GOGO","SHEN",
    ],
    "5G & Satellite": [
        "IRDM","GILT","OOMA","VIAV","COMM","DGII","CLFD","SWIR",
    ],
    # ── Business & Data ──
    "HR & Payroll": [
        "CDAY","PAYC","PCTY","PRFT","RHI","HEIDRICK","ASGN","KELYA",
        "KFY","HSII",
    ],
    "Consulting & IT Svc": [
        "ASGN","DXC","EPAM","GLOB","NSIT","PRFT","TASK","TTEC","WIT",
        "CTSH","INFY","HCL",
    ],
    "Data & Analytics": [
        "DNB","PRFT","TRU","NLSN","FOUR","ZI","CWAN","DDOG","ESTC",
        "MDB","NEWR","SPLK","SUMO","TRI",
    ],
    "Business Process": [
        "CSGP","FIS","GPN","WEX","BKI","EVTC","FLUT","JKHY","NCR",
        "NCRI","NAVI","PAY","SLM","VRRM","DSP",
    ],
}

# ── 3. 필터링: 이미 존재하는 종목 제거 ──────────────────────────────
candidates_flat = {}
for sub, tickers in CANDIDATES.items():
    new = [t for t in tickers if t not in existing]
    if new:
        candidates_flat[sub] = sorted(set(new))

total_candidates = sum(len(v) for v in candidates_flat.values())
print(f"[INFO] Candidate tickers (after dedup): {total_candidates}")

# ── 4. yfinance 교차검증 (배치 다운로드) ─────────────────────────────
try:
    import yfinance as yf
except ImportError:
    print("[ERROR] yfinance not installed. pip install yfinance")
    sys.exit(1)

all_candidates = sorted(set(t for tickers in candidates_flat.values() for t in tickers))
print(f"[INFO] Validating {len(all_candidates)} unique tickers via yfinance...")

BATCH = 100
valid_tickers = set()
invalid_tickers = set()

for i in range(0, len(all_candidates), BATCH):
    batch = all_candidates[i:i+BATCH]
    batch_str = " ".join(batch)
    try:
        data = yf.download(batch_str, period="5d", progress=False, threads=True)
        if data.empty:
            invalid_tickers.update(batch)
            continue
        # Check which tickers returned valid data
        if isinstance(data.columns, __import__('pandas').MultiIndex):
            for t in batch:
                if t in data.columns.get_level_values(1):
                    col = data[('Close', t)]
                    if col.notna().any():
                        valid_tickers.add(t)
                    else:
                        invalid_tickers.add(t)
                else:
                    invalid_tickers.add(t)
        else:
            # Single ticker
            if data['Close'].notna().any():
                valid_tickers.update(batch)
            else:
                invalid_tickers.update(batch)
    except Exception as e:
        print(f"  [WARN] Batch {i//BATCH+1} error: {e}")
        invalid_tickers.update(batch)

    pct = min(100, (i + BATCH) / len(all_candidates) * 100)
    print(f"  ... {pct:.0f}% ({len(valid_tickers)} valid, {len(invalid_tickers)} invalid)")
    time.sleep(0.5)

print(f"\n[RESULT] Valid: {len(valid_tickers)}, Invalid: {len(invalid_tickers)}")
if invalid_tickers:
    print(f"[REMOVED] {sorted(invalid_tickers)}")

# ── 5. 결과 출력 (섹터별 검증 완료 종목) ────────────────────────────
result = {}
for sub, tickers in candidates_flat.items():
    validated = sorted([t for t in tickers if t in valid_tickers])
    if validated:
        result[sub] = validated

print(f"\n{'='*60}")
print(f"VALIDATED ADDITIONS BY SUB-SECTOR")
print(f"{'='*60}")
total_valid = 0
for sub, tickers in sorted(result.items()):
    total_valid += len(tickers)
    print(f"\n# {sub} (+{len(tickers)})")
    # Print in code-ready format
    joined = '","'.join(tickers)
    print(f'  ["{joined}"]')

print(f"\n{'='*60}")
print(f"TOTAL VALIDATED ADDITIONS: {total_valid}")
print(f"NEW TOTAL (existing {len(existing)} + new {total_valid}): {len(existing) + total_valid}")

# ── 6. JSON 결과 저장 ───────────────────────────────────────────────
out_path = Path(__file__).parent / "us_expansion_validated.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({"valid": result, "invalid": sorted(invalid_tickers)}, f, indent=2)
print(f"\n[SAVED] {out_path}")
