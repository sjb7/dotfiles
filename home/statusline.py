#!/usr/bin/env python3
"""Universal status line script for Antigravity (agy), Claude Code, and Grok CLI.

Strictly isolates each client and displays usage limits according to how that
platform and active model calculate quotas:
  - Antigravity (agy): 5-hour (5h) + Weekly (Wk) per model family (gemini vs 3p) + Credits
  - Claude Code:       5-hour (5h) + 7-day Weekly (Wk) + Per-model Weekly (Opus/Sonnet/Fable) + Monthly extra/spend limit (Mo)
  - Grok CLI:          Weekly credit pool (Wk) + Monthly spend/cap limit (Mo)
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import subprocess
import sys
import time
import urllib.request

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
BLUE = "\033[34m"

AGY_QUOTA_CACHE = os.path.expanduser("~/.gemini/antigravity-cli/.statusline-quota.json")
CLAUDE_QUOTA_CACHE = os.path.expanduser("~/.claude/.statusline-quota.json")
CLAUDE_STATE_FILE = os.path.expanduser("~/.claude.json")
GROK_QUOTA_CACHE = os.path.expanduser("~/.grok/.statusline-quota-v2.json")
GROK_QUOTA_TTL_SECS = 60


def color_by_pct(pct: float, invert: bool = False) -> str:
    """Return ANSI color for a percentage. If invert=True, higher % is better (e.g. remaining quota)."""
    used = (100.0 - pct) if invert else pct
    if used >= 80:
        return RED
    if used >= 50:
        return YELLOW
    return GREEN


def progress_bar(pct: float, width: int = 8) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = max(0, min(width, round((pct / 100.0) * width)))
    return "▰" * filled + f"{DIM}{'▱' * (width - filled)}{RESET}"


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def fmt_duration(seconds: float | int | None) -> str:
    if seconds is None or seconds <= 0:
        return ""
    m, _ = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    if d > 0:
        return f"{d}d{h}h"
    if h > 0:
        return f"{h}h{m}m"
    return f"{m}m"


def parse_iso_reset_seconds(iso_str: str | int | float | None) -> int | None:
    if iso_str is None:
        return None
    if isinstance(iso_str, (int, float)):
        diff = float(iso_str) - time.time()
        return int(diff) if diff > 0 else 0
    if not isinstance(iso_str, str) or not iso_str.strip():
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        diff = (dt - datetime.now(timezone.utc)).total_seconds()
        return int(diff) if diff > 0 else 0
    except Exception:
        return None


def save_json_cache(path: str, payload: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
    except Exception:
        pass


def load_json_cache(path: str) -> dict | None:
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def detect_client(data: dict, model_name: str) -> str:
    """Strictly classify which CLI invoked statusline.py from stdin JSON shape."""
    tp = str(data.get("transcript_path") or "")
    # 1. Antigravity CLI (agy)
    if (
        "conversation_id" in data
        or "quota" in data
        or "cycle_mode" in data
        or "vcs" in data
        or "antigravity-cli" in tp
    ):
        return "antigravity"

    # 2. Grok CLI (grok)
    ctx = data.get("context_window") or {}
    ws = data.get("workspace") or {}
    if (
        "schema_version" in data
        or "context_tokens" in ctx
        or "session_input_tokens" in ctx
        or "repo_root" in ws
        or "/.grok/" in tp
        or model_name.lower().startswith("grok")
    ):
        return "grok"

    # 3. Claude Code (claude)
    if (
        "output_style" in data
        or "rate_limits" in data
        or "prompt_cache" in data
        or "/.claude/" in tp
        or any(k in model_name.lower() for k in ("claude", "opus", "sonnet", "haiku", "fable"))
    ):
        return "claude"

    return "unknown"


# ---------------------------------------------------------------------------
# 1. Antigravity (agy) Quota: 5h + Weekly per model group (gemini vs 3p)
# ---------------------------------------------------------------------------
def format_agy_quota(data: dict, model_name: str) -> list[str]:
    quota = data.get("quota")
    if isinstance(quota, dict) and quota:
        save_json_cache(AGY_QUOTA_CACHE, {"quota": quota, "saved_at": time.time()})
    else:
        cached = load_json_cache(AGY_QUOTA_CACHE)
        quota = (cached or {}).get("quota") if cached else None

    if not isinstance(quota, dict) or not quota:
        return []

    is_3p = any(k in model_name.lower() for k in ("claude", "gpt", "sonnet", "opus", "haiku", "o1", "o3"))
    k_5h = "3p-5h" if is_3p and "3p-5h" in quota else "gemini-5h"
    k_wk = "3p-weekly" if is_3p and "3p-weekly" in quota else "gemini-weekly"

    items: list[str] = []
    for label, key in (("5h", k_5h), ("Wk", k_wk)):
        bucket = quota.get(key)
        if not isinstance(bucket, dict) or bucket.get("disabled"):
            continue
        if bucket.get("remaining_fraction") is None:
            continue
        rem = max(0.0, min(100.0, float(bucket["remaining_fraction"]) * 100.0))
        col = color_by_pct(rem, invert=True)
        # Prefer ISO reset_time so cached buckets stay accurate over time
        rst_sec = parse_iso_reset_seconds(bucket.get("reset_time"))
        if rst_sec is None:
            rst_sec = bucket.get("reset_in_seconds")
        rst = fmt_duration(rst_sec)
        rst_str = f" {DIM}↻{rst}{RESET}" if rst else ""
        items.append(f"{label}:{col}{rem:.0f}%{RESET}{rst_str}")

    for b_val in quota.values():
        if isinstance(b_val, dict) and b_val.get("remaining_amount") is not None and not b_val.get("disabled"):
            items.append(f"Cr:{BLUE}{b_val['remaining_amount']}{RESET}")

    return items


# ---------------------------------------------------------------------------
# 2. Claude Code Quota: 5h + 7d Weekly + Per-Model Weekly + Monthly Extra/Spend
# ---------------------------------------------------------------------------
def load_claude_utilization() -> dict | None:
    """Read Claude Code's own cachedUsageUtilization from ~/.claude.json if fresh."""
    state = load_json_cache(CLAUDE_STATE_FILE)
    if not state:
        return None
    cu = state.get("cachedUsageUtilization")
    if not isinstance(cu, dict):
        return None
    util = cu.get("utilization")
    return util if isinstance(util, dict) else None


def format_claude_quota(data: dict, model_name: str) -> list[str]:
    rl = data.get("rate_limits")
    if isinstance(rl, dict) and rl:
        save_json_cache(CLAUDE_QUOTA_CACHE, {"rate_limits": rl, "saved_at": time.time()})
    else:
        cached = load_json_cache(CLAUDE_QUOTA_CACHE)
        if cached and isinstance(cached.get("rate_limits"), dict):
            rl = cached["rate_limits"]

    util = load_claude_utilization()
    items: list[str] = []
    now = time.time()

    # A. 5-hour session limit
    fh = (rl or {}).get("five_hour") if isinstance(rl, dict) else None
    if not fh and isinstance(util, dict) and isinstance(util.get("five_hour"), dict):
        u5 = util["five_hour"]
        if u5.get("utilization") is not None:
            fh = {"used_percentage": u5["utilization"], "resets_at": u5.get("resets_at")}

    if isinstance(fh, dict) and fh.get("used_percentage") is not None:
        rst_sec = parse_iso_reset_seconds(fh.get("resets_at"))
        if rst_sec is None or rst_sec > 0:
            rem5 = max(0.0, min(100.0, 100.0 - float(fh["used_percentage"])))
            col5 = color_by_pct(rem5, invert=True)
            rst5 = fmt_duration(rst_sec)
            rst_str = f" {DIM}↻{rst5}{RESET}" if rst5 else ""
            items.append(f"5h:{col5}{rem5:.0f}%{RESET}{rst_str}")

    # B. 7-day weekly limit (overall)
    sd = (rl or {}).get("seven_day") if isinstance(rl, dict) else None
    if not sd and isinstance(util, dict) and isinstance(util.get("seven_day"), dict):
        u7 = util["seven_day"]
        if u7.get("utilization") is not None:
            sd = {"used_percentage": u7["utilization"], "resets_at": u7.get("resets_at")}

    if isinstance(sd, dict) and sd.get("used_percentage") is not None:
        rst_sec = parse_iso_reset_seconds(sd.get("resets_at"))
        if rst_sec is None or rst_sec > 0:
            remw = max(0.0, min(100.0, 100.0 - float(sd["used_percentage"])))
            colw = color_by_pct(remw, invert=True)
            rstw = fmt_duration(rst_sec)
            rst_str = f" {DIM}↻{rstw}{RESET}" if rstw else ""
            items.append(f"Wk:{colw}{remw:.0f}%{RESET}{rst_str}")

    # C. Model-specific weekly limit (Opus / Sonnet / Fable) when present
    if isinstance(util, dict):
        m_low = model_name.lower()
        scoped_candidates: list[tuple[str, float, str | int | float | None]] = []
        if "opus" in m_low and isinstance(util.get("seven_day_opus"), dict):
            so = util["seven_day_opus"]
            if so.get("utilization") is not None:
                scoped_candidates.append(("Opus", float(so["utilization"]), so.get("resets_at")))
        elif "sonnet" in m_low and isinstance(util.get("seven_day_sonnet"), dict):
            ss = util["seven_day_sonnet"]
            if ss.get("utilization") is not None:
                scoped_candidates.append(("Sonnet", float(ss["utilization"]), ss.get("resets_at")))

        for row in util.get("limits") or []:
            if isinstance(row, dict) and row.get("kind") == "weekly_scoped":
                s_name = ((row.get("scope") or {}).get("model") or {}).get("display_name")
                if s_name and s_name.lower() in m_low and row.get("percent") is not None:
                    scoped_candidates.append((s_name, float(row["percent"]), row.get("resets_at")))

        if scoped_candidates:
            s_label, s_used, s_rst = scoped_candidates[0]
            rst_sec = parse_iso_reset_seconds(s_rst)
            if rst_sec is None or rst_sec > 0:
                s_rem = max(0.0, min(100.0, 100.0 - s_used))
                s_col = color_by_pct(s_rem, invert=True)
                items.append(f"{s_label}:{s_col}{s_rem:.0f}%{RESET}")

    # D. Monthly / Gateway Spend Limit (`spend_limit` or `extra_usage`)
    sp = (rl or {}).get("spend_limit") if isinstance(rl, dict) else None
    if isinstance(sp, dict) and sp.get("used_percentage") is not None:
        rst_sec = parse_iso_reset_seconds(sp.get("resets_at"))
        if rst_sec is None or rst_sec > 0:
            rem_s = max(0.0, 100.0 - float(sp["used_percentage"]))
            col_s = color_by_pct(rem_s, invert=True)
            period_map = {"monthly": "Mo", "weekly": "Wk", "daily": "Day"}
            p_lbl = period_map.get(str(sp.get("period") or "").lower(), "Mo")
            usd_detail = ""
            if sp.get("used_usd") is not None and sp.get("limit_usd") is not None:
                usd_detail = f" {DIM}(${float(sp['used_usd']):.0f}/${float(sp['limit_usd']):.0f}){RESET}"
            rst_s = fmt_duration(rst_sec)
            rst_str = f" {DIM}↻{rst_s}{RESET}" if rst_s else ""
            items.append(f"{p_lbl}:{col_s}{rem_s:.0f}%{RESET}{usd_detail}{rst_str}")
    elif isinstance(util, dict) and isinstance(util.get("extra_usage"), dict):
        eu = util["extra_usage"]
        if eu.get("is_enabled") and eu.get("utilization") is not None:
            rem_e = max(0.0, 100.0 - float(eu["utilization"]))
            col_e = color_by_pct(rem_e, invert=True)
            usd_detail = ""
            if eu.get("used_credits") is not None and eu.get("monthly_limit") is not None:
                used_d = float(eu["used_credits"]) / 100.0
                lim_d = float(eu["monthly_limit"]) / 100.0
                usd_detail = f" {DIM}(${used_d:.0f}/${lim_d:.0f}){RESET}"
            items.append(f"Mo:{col_e}{rem_e:.0f}%{RESET}{usd_detail}")

    return items


# ---------------------------------------------------------------------------
# 3. Grok CLI Quota: Weekly Credit Pool (Wk) + Monthly Spend/Cap Limit (Mo)
# ---------------------------------------------------------------------------
def grok_auth_key() -> str | None:
    path = os.path.join(os.environ.get("GROK_HOME") or os.path.expanduser("~/.grok"), "auth.json")
    data = load_json_cache(path)
    if not data:
        return None
    for value in data.values():
        if isinstance(value, dict) and value.get("key"):
            return str(value["key"])
    return None


def fetch_grok_quota() -> dict | None:
    key = grok_auth_key()
    if not key:
        return None
    base = (os.environ.get("GROK_CLI_CHAT_PROXY_BASE_URL") or "https://cli-chat-proxy.grok.com/v1").rstrip("/")
    headers = {
        "Authorization": f"Bearer {key}",
        "X-XAI-Token-Auth": "xai-grok-cli",
        "Accept": "application/json",
        "User-Agent": "xai-grok-cli",
    }

    result: dict = {"fetched_at": time.time()}

    # 1. Weekly (or active period) credit pool from /billing?format=credits
    try:
        req1 = urllib.request.Request(f"{base}/billing?format=credits", headers=headers)
        with urllib.request.urlopen(req1, timeout=3.5) as resp1:
            p1 = json.loads(resp1.read().decode("utf-8", "replace"))
        cfg1 = p1.get("config") if isinstance(p1, dict) else None
        if isinstance(cfg1, dict) and cfg1.get("creditUsagePercent") is not None:
            period = cfg1.get("currentPeriod") if isinstance(cfg1.get("currentPeriod"), dict) else {}
            ptype = str(period.get("type") or "").upper()
            p_end = period.get("end") or cfg1.get("billingPeriodEnd")
            build_pct = None
            for row in cfg1.get("productUsage") or []:
                if isinstance(row, dict) and row.get("product") == "GrokBuild" and row.get("usagePercent") is not None:
                    build_pct = float(row["usagePercent"])
                    break
            result["credits"] = {
                "used_percent": float(cfg1["creditUsagePercent"]),
                "period_type": "Mo" if "MONTH" in ptype else "Wk",
                "period_end": p_end,
                "build_used_percent": build_pct,
            }
    except Exception:
        pass

    # 2. Monthly limit from /billing
    try:
        req2 = urllib.request.Request(f"{base}/billing", headers=headers)
        with urllib.request.urlopen(req2, timeout=3.5) as resp2:
            p2 = json.loads(resp2.read().decode("utf-8", "replace"))
        cfg2 = p2.get("config") if isinstance(p2, dict) else None
        if isinstance(cfg2, dict):
            m_lim = (cfg2.get("monthlyLimit") or {}).get("val")
            m_used = (cfg2.get("used") or {}).get("val")
            od_cap = (cfg2.get("onDemandCap") or {}).get("val")
            od_used = (cfg2.get("onDemandUsed") or {}).get("val")
            m_end = cfg2.get("billingPeriodEnd")
            if m_lim is not None and m_used is not None:
                result["monthly"] = {
                    "limit_cents": int(m_lim),
                    "used_cents": int(m_used),
                    "ondemand_cap_cents": int(od_cap or 0),
                    "ondemand_used_cents": int(od_used or 0),
                    "period_end": m_end,
                }
    except Exception:
        pass

    return result if ("credits" in result or "monthly" in result) else None


def load_grok_quota(trigger: str | None) -> dict | None:
    cached = load_json_cache(GROK_QUOTA_CACHE)
    age = (time.time() - float(cached.get("fetched_at") or 0)) if cached else None
    fresh = age is not None and age < GROK_QUOTA_TTL_SECS
    if trigger != "refresh_interval" and fresh:
        return cached
    live = fetch_grok_quota()
    if live:
        save_json_cache(GROK_QUOTA_CACHE, live)
        return live
    return cached


def format_grok_quota(data: dict) -> list[str]:
    gq = load_grok_quota(data.get("trigger"))
    if not isinstance(gq, dict):
        return []

    items: list[str] = []

    # 1. Weekly credit pool (Wk)
    cr = gq.get("credits")
    if isinstance(cr, dict) and cr.get("used_percent") is not None:
        used = float(cr["used_percent"])
        rem = max(0.0, min(100.0, 100.0 - used))
        col = color_by_pct(rem, invert=True)
        lbl = cr.get("period_type") or "Wk"
        rst = fmt_duration(parse_iso_reset_seconds(cr.get("period_end")))
        rst_str = f" {DIM}↻{rst}{RESET}" if rst else ""
        items.append(f"{lbl}:{col}{rem:.0f}%{RESET}{rst_str}")

    # 2. Monthly limit (Mo)
    mo = gq.get("monthly")
    if isinstance(mo, dict):
        lim_c = int(mo.get("limit_cents") or 0) + int(mo.get("ondemand_cap_cents") or 0)
        used_c = int(mo.get("used_cents") or 0) + int(mo.get("ondemand_used_cents") or 0)
        if lim_c > 0:
            used_pct = (used_c / lim_c) * 100.0
            rem_m = max(0.0, min(100.0, 100.0 - used_pct))
            col_m = color_by_pct(rem_m, invert=True)
            rst_m = fmt_duration(parse_iso_reset_seconds(mo.get("period_end")))
            rst_str = f" {DIM}↻{rst_m}{RESET}" if rst_m else ""
            used_d = used_c / 100.0
            lim_d = lim_c / 100.0
            items.append(
                f"Mo:{col_m}{rem_m:.0f}%{RESET} {DIM}(${used_d:.2f}/${lim_d:.0f}){RESET}{rst_str}"
            )

    return items


def detect_git(cwd: str | None) -> tuple[str | None, bool]:
    if not cwd or not os.path.isdir(cwd):
        return None, False
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        branch = subprocess.check_output(
            ["git", "-C", cwd, "symbolic-ref", "--quiet", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=0.08,
            env=env,
        ).decode("utf-8", "ignore").strip()
        if not branch:
            return None, False
        status = subprocess.check_output(
            ["git", "-C", cwd, "status", "--porcelain", "-uno"],
            stderr=subprocess.DEVNULL,
            timeout=0.08,
            env=env,
        ).decode("utf-8", "ignore").strip()
        return branch, bool(status)
    except Exception:
        return None, False


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        data = json.loads(raw)
    except Exception:
        return

    parts: list[str] = []

    # 1. Model & Reasoning Effort
    model = data.get("model") or {}
    if isinstance(model, str):
        m_name, effort = model, None
    else:
        m_name = model.get("display_name") or model.get("id") or "AI"
        effort = model.get("effort")

    if not effort:
        eff_obj = data.get("effort")
        if isinstance(eff_obj, dict):
            effort = eff_obj.get("level")
        elif isinstance(eff_obj, str):
            effort = eff_obj
    if not effort:
        effort = data.get("reasoning_effort")

    client = detect_client(data, m_name)

    m_str = f"{CYAN}{BOLD}{m_name}{RESET}"
    if effort:
        m_str += f" {DIM}({effort}){RESET}"
    parts.append(m_str)

    # 2. Context Window Usage + Tokens + Cache Hit
    ctx = data.get("context_window") or {}
    max_tokens = int(ctx.get("context_window_size") or 0)
    cur = ctx.get("current_usage") or {}
    in_tok = int(cur.get("input_tokens") or 0)
    out_tok = int(cur.get("output_tokens") or 0)
    cache_read = int(cur.get("cache_read_input_tokens") or 0)
    cache_create = int(cur.get("cache_creation_input_tokens") or 0)

    if ctx.get("context_tokens") is not None:
        active_tokens = int(ctx.get("context_tokens") or 0)
    elif (in_tok + out_tok + cache_read + cache_create) > 0:
        active_tokens = in_tok + out_tok + cache_read + cache_create
    else:
        active_tokens = int(ctx.get("total_input_tokens") or 0) + int(ctx.get("total_output_tokens") or 0)

    used_pct = ctx.get("used_percentage")
    if used_pct is None and max_tokens > 0 and active_tokens > 0:
        used_pct = (active_tokens / max_tokens) * 100.0
    used_pct = float(used_pct or 0.0)

    if active_tokens == 0 and used_pct > 0 and max_tokens > 0:
        active_tokens = int(max_tokens * (used_pct / 100.0))

    c_col = color_by_pct(used_pct)
    bar = progress_bar(used_pct, width=8)
    if max_tokens > 0:
        tok_detail = f"{fmt_tokens(active_tokens)}/{fmt_tokens(max_tokens)}"
    elif active_tokens > 0:
        tok_detail = fmt_tokens(active_tokens)
    else:
        tok_detail = ""

    prompt_total = in_tok + cache_read + cache_create
    pc = data.get("prompt_cache") or {}
    if cache_read > 0 and prompt_total > 0:
        cache_pct = int(round((cache_read / prompt_total) * 100))
        tok_detail = f"{tok_detail} ⚡{cache_pct}%" if tok_detail else f"⚡{cache_pct}%"
    elif isinstance(pc, dict) and pc.get("hit_ratio") is not None and float(pc["hit_ratio"]) > 0:
        cache_pct = int(round(float(pc["hit_ratio"]) * 100))
        tok_detail = f"{tok_detail} ⚡{cache_pct}%" if tok_detail else f"⚡{cache_pct}%"

    ctx_str = f"Ctx {c_col}{bar} {used_pct:.0f}%{RESET}"
    if tok_detail:
        ctx_str += f" {DIM}({tok_detail}){RESET}"
    parts.append(ctx_str)

    # 3. Client-Specific Quota / Usage Limits
    q_items: list[str] = []
    if client == "antigravity":
        q_items = format_agy_quota(data, m_name)
    elif client == "claude":
        q_items = format_claude_quota(data, m_name)
    elif client == "grok":
        q_items = format_grok_quota(data)

    if q_items:
        parts.append("Quota " + f" {DIM}·{RESET} ".join(q_items))

    # 4. Session Cost (`total_usd` in agy, `total_cost_usd` in claude/grok)
    cost = data.get("cost") or {}
    total_usd = cost.get("total_usd") if cost.get("total_usd") is not None else cost.get("total_cost_usd")
    if total_usd is not None and float(total_usd) >= 0.005:
        parts.append(f"{MAGENTA}${float(total_usd):.2f}{RESET}")

    # 5. VCS / Git Branch & Dirty Status
    vcs = data.get("vcs") or {}
    ws = data.get("workspace") or {}
    branch = vcs.get("branch") or ws.get("branch") or ws.get("git_worktree")
    dirty = bool(vcs.get("dirty"))
    if not branch:
        cwd = ws.get("current_dir") or data.get("cwd")
        branch, dirty = detect_git(cwd)

    if branch:
        dirty_mark = f"{YELLOW}*{RESET}" if dirty else ""
        parts.append(f"🌿 {branch}{dirty_mark}")

    # 6. Active Subagents / Background Tasks / Sandbox / Vim Mode
    badges: list[str] = []
    subagents = data.get("subagents") or []
    running_subs = [
        s for s in subagents
        if isinstance(s, dict) and s.get("status", "").lower() in ("running", "working", "active")
    ]
    if running_subs:
        badges.append(f"🤖 {len(running_subs)}")

    tasks = int(data.get("task_count") or 0)
    if tasks > 0:
        badges.append(f"⚙ {tasks}")

    if (data.get("sandbox") or {}).get("enabled"):
        badges.append("🔒")

    vim_mode = (data.get("vim") or {}).get("mode")
    if vim_mode:
        badges.append(f"{DIM}[{vim_mode}]{RESET}")

    if badges:
        parts.append(" ".join(badges))

    sep = f" {DIM}│{RESET} "
    print(sep.join(parts))


if __name__ == "__main__":
    main()
