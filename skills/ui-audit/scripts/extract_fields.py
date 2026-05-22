#!/usr/bin/env python3
"""extract_fields.py — HTML/JS/CSS에서 UI 필드와 스타일 토큰을 추출한다.

사용법:
    python extract_fields.py <web_app_dir>

출력: JSON (stdout)
    {
      "html_ids": { "파일명": [{"id": "...", "tag": "...", "line": N}, ...] },
      "js_bindings": { "파일명": [{"id": "...", "field": "...", "line": N}, ...] },
      "js_field_refs": { "파일명": [{"field": "...", "line": N}, ...] },
      "inline_styles": { "파일명": [{"style": "...", "line": N}, ...] },
      "css_vars_defined": ["--var-name", ...],
      "css_vars_used": { "파일명": [{"var": "--var-name", "line": N}, ...] }
    }
"""
import sys
import os
import re
import json
import glob


def extract_html_ids(filepath):
    """HTML 파일에서 id 속성을 가진 모든 요소를 추출."""
    results = []
    id_pat = re.compile(r'<(\w+)\s[^>]*id\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
    with open(filepath, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            for m in id_pat.finditer(line):
                results.append({"tag": m.group(1), "id": m.group(2), "line": i})
    return results


def extract_js_bindings(filepath):
    """JS에서 setText('id', d.Field) 패턴의 데이터 바인딩을 추출."""
    bindings = []
    field_refs = []
    # setText('detail-price', d.Price)
    set_pat = re.compile(r"setText\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*(?:d|stock|data)\.(\w+)")
    # document.getElementById('detail-price')
    getid_pat = re.compile(r"getElementById\s*\(\s*['\"]([^'\"]+)['\"]")
    # d.FieldName or stock.FieldName 참조
    field_pat = re.compile(r"(?:d|stock|data)\.(\w+)")

    with open(filepath, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            for m in set_pat.finditer(line):
                bindings.append({"id": m.group(1), "field": m.group(2), "line": i})
            for m in getid_pat.finditer(line):
                if not set_pat.search(line):
                    bindings.append({"id": m.group(1), "field": None, "line": i})
            for m in field_pat.finditer(line):
                field_refs.append({"field": m.group(1), "line": i})

    # deduplicate field_refs by field name
    seen = set()
    unique_refs = []
    for r in field_refs:
        if r["field"] not in seen:
            seen.add(r["field"])
            unique_refs.append(r)

    return bindings, unique_refs


def extract_inline_styles(filepath):
    """HTML/JS에서 인라인 style 속성을 추출."""
    results = []
    style_pat = re.compile(r'style\s*=\s*["\']([^"\']{10,})["\']')
    with open(filepath, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            for m in style_pat.finditer(line):
                results.append({"style": m.group(1), "line": i})
    return results


def extract_css_vars(filepath):
    """CSS 파일에서 정의된 변수와 사용처를 추출."""
    defined = []
    used = []
    def_pat = re.compile(r"(--[\w-]+)\s*:")
    use_pat = re.compile(r"var\((--[\w-]+)\)")

    with open(filepath, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            for m in def_pat.finditer(line):
                defined.append(m.group(1))
            for m in use_pat.finditer(line):
                used.append({"var": m.group(1), "line": i})

    return list(set(defined)), used


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "사용법: python extract_fields.py <web_app_dir>"}))
        sys.exit(1)

    base = sys.argv[1]
    if not os.path.isdir(base):
        print(json.dumps({"error": f"디렉토리 없음: {base}"}))
        sys.exit(1)

    result = {
        "html_ids": {},
        "js_bindings": {},
        "js_field_refs": {},
        "inline_styles": {},
        "css_vars_defined": [],
        "css_vars_used": {},
    }

    # HTML files
    for fp in glob.glob(os.path.join(base, "**", "*.html"), recursive=True):
        key = os.path.relpath(fp, base).replace("\\", "/")
        ids = extract_html_ids(fp)
        if ids:
            result["html_ids"][key] = ids
        styles = extract_inline_styles(fp)
        if styles:
            result["inline_styles"][key] = styles

    # JS files
    for fp in glob.glob(os.path.join(base, "**", "*.js"), recursive=True):
        key = os.path.relpath(fp, base).replace("\\", "/")
        bindings, refs = extract_js_bindings(fp)
        if bindings:
            result["js_bindings"][key] = bindings
        if refs:
            result["js_field_refs"][key] = refs
        styles = extract_inline_styles(fp)
        if styles:
            result["inline_styles"].setdefault(key, []).extend(styles)

    # CSS files
    all_defined = []
    for fp in glob.glob(os.path.join(base, "**", "*.css"), recursive=True):
        key = os.path.relpath(fp, base).replace("\\", "/")
        defined, used = extract_css_vars(fp)
        all_defined.extend(defined)
        if used:
            result["css_vars_used"][key] = used
    result["css_vars_defined"] = sorted(set(all_defined))

    # Also check var() usage in HTML inline styles
    for fp in glob.glob(os.path.join(base, "**", "*.html"), recursive=True):
        key = os.path.relpath(fp, base).replace("\\", "/")
        _, used = extract_css_vars(fp)
        if used:
            result["css_vars_used"].setdefault(key, []).extend(used)

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
