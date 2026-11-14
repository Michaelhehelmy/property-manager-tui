from __future__ import annotations

import json
import logging
from typing import Any

from .common import _fmt_money, _format_data, _iso, _short, _today

logger = logging.getLogger(__name__)

# ── Friendly formatting & deterministic synthesis (local, no deps) ───────────

def _format_data(title: str, data: Any) -> str:
    """Fallback: compact structured view (kept for traceability)."""
    try:
        body = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    except Exception:
        body = str(data)
    if len(body) > 6000:
        body = body[:6000] + "\n... (truncated)"
    return f"{title}\n{body}"

def _friendly_open(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "Sure — here's what I found based on real app data."
    return text

def _fmt_capabilities(data: dict) -> str:
    caps = (data or {}).get("capabilities") or []
    notes = (data or {}).get("notes") or []
    lines = ["Sure — I’m your grounded manager assistant and business partner (read-only, based on real app data). I can look things up, summarize performance, and guide you with examples."]
    lines.append("")
    lines.append("**What I can help with**")
    for c in caps:
        lines.append(f"- {c}")
    if notes:
        lines.append("")
        for n in notes: lines.append(f"**Note:** {n}")
    lines.append("")
    lines.append("**How to use (examples you can ask)**")
    lines.append("- **Business & ops:** `business partner briefing for this month`, `how are we doing?`, `today's ops`, `property stats`, `housekeeping status`, `reservation pipeline`, `available rooms tomorrow`")
    lines.append("- **Money & reports:** `who owes money?`, `P&L last month`, `cash flow this month`, `VAT 2026-11-01 to 2026-11-30`, `payment methods this month`, `occupancy last month`, `compare this month vs last month`, `expenses breakdown last month`, `top selling items this month`, `sales breakdown this week`")
    lines.append("- **Inventory & catalog:** `any low stock items?`, `top selling items`, `what expires soon?`, `expiring batches in 7 days`, `list inventory batches`, `trace barcode LOT-...` / `trace SKU ...` (lot received/expiry/movements), `menu`, `rate plans`, `list registered activities`, `activity booking statistics`")
    lines.append("- **Guests (360°):** `find guest Ahmed`, `guest #3 profile`, `show guest #3 full portfolio` (all stays, POS/orders, payments, invoices, totals), `when was guest #3 last visit?`, `what did guest #3 order most on last visit?`, `guest #3 last visit spend`, `what activities did guest #3 book?`")
    lines.append("- **Reservations:** `reservation #12 summary`, `reservation #12 balance`")
    lines.append("- **Health, doctor & updates:** `ledger integrity check`, `doctor report` / `health check` / `backup restore readiness`, `batch stock mismatch`, `recent activity`, `check for updates` / `what app version`")
    lines.append("")
    lines.append("**Tips**")
    lines.append("- Be specific when you can: use IDs (`guest #3`, `reservation #12`), names (`find guest Sara`), report names, or date ranges (`2026-10-01 to 2026-10-31`, `last month`, `this week`).")
    lines.append("- If I’m unsure or find multiple matches, I’ll ask you to clarify — reply with the ID I suggest.")
    lines.append("- I only read real app data (no changes, no guesses). If something is missing for the period, I’ll tell you.")
    lines.append("- You can rate answers and correct my intent in the app so I learn your phrasing over time.")
    return "\n".join(lines)

def _fmt_stats(data: dict) -> str:
    stats = (data or {}).get("stats") or {}
    as_of = (data or {}).get("as_of") or _iso(_today())
    if not stats:
        return "I couldn’t retrieve property stats right now. Want me to try again or check today’s ops?"
    lines = [f"Sure — here’s your property snapshot (as of {as_of}, from real records):"]
    if "guests_total" in stats: lines.append(f"- Guests: **{stats['guests_total']}** total")
    if "rooms_total" in stats: lines.append(f"- Rooms: **{stats['rooms_total']}** total")
    if "reservations_total" in stats: lines.append(f"- Reservations: **{stats['reservations_total']}** total")
    by = stats.get("reservations_by_status") or {}
    if by:
        parts = [f"{k}: {v}" for k,v in by.items()]
        lines.append(f"- By status: {', '.join(parts)}")
    if "in_house" in stats: lines.append(f"- In-house now (Checked In): **{stats['in_house']}**")
    if "arrivals_today" in stats: lines.append(f"- Arrivals today: **{stats['arrivals_today']}**")
    if "departures_today" in stats: lines.append(f"- Departures today: **{stats['departures_today']}**")
    return "\n".join(lines)

def _fmt_today_ops(data: dict) -> str:
    if data.get("error"):
        return f"I couldn’t load today’s ops right now ({data.get('error')}). Want to try property stats instead?"
    date = data.get("date") or _iso(_today())
    fd = data.get("front_desk") or {}
    hk = data.get("housekeeping") or {}
    ops = data.get("daily_ops")
    lines = [f"Here’s today’s operations snapshot for **{date}** (grounded in real data):"]
    if fd:
        lines.append(f"- Front desk: arrivals **{fd.get('arrivals_today',0)}**, departures **{fd.get('departures_today',0)}**, in-house **{fd.get('in_house', fd.get('inhouse', 0))}**, occupancy **{fd.get('occupancy_pct',0)}%**")
        lines.append(f"- Rooms readiness: clean **{fd.get('clean',0)}**, dirty **{fd.get('dirty',0)}**, maintenance **{fd.get('maint',0)}**")
    if hk and hk.get("by_status"):
        parts = [f"{k}: {v}" for k,v in (hk.get("by_status") or {}).items()]
        lines.append(f"- Housekeeping: {', '.join(parts)} (total {hk.get('total_rooms',0)})")
    if isinstance(ops, dict):
        # summarize common keys
        for k in ("arrivals","departures","in_house","stayovers"):
            v = ops.get(k)
            if v is not None and k not in ("",):
                if isinstance(v, list): lines.append(f"- {k.replace('_',' ').title()}: **{len(v)}**")
                elif isinstance(v, (int,float,str)): lines.append(f"- {k.replace('_',' ').title()}: **{v}**")
    elif isinstance(ops, str) and ops.strip():
        # include concise excerpt
        excerpt = "\n".join([ln for ln in ops.strip().splitlines() if ln.strip()][:12])
        lines.append("")
        lines.append("Ops details:")
        lines.append(excerpt)
    if not fd and not hk and not ops:
        lines.append("- No ops details were returned for today.")
    return "\n".join(lines)

def _fmt_partner_briefing(data: dict) -> str:
    ins = (data or {}).get("insights") or {}
    start, end = data.get("start"), data.get("end")
    out = [f"Here’s your business partner briefing for **{start} → {end}** (grounded in real data):"]
    if ins.get("snapshot"):
        out.append("\n**Snapshot**"); out += [f"- {x}" for x in ins["snapshot"]]
    if ins.get("good"):
        out.append("\n**What’s going well**"); out += [f"- {x}" for x in ins["good"]]
    if ins.get("watchouts"):
        out.append("\n**Watch-outs**"); out += [f"- {x}" for x in ins["watchouts"]]
    else:
        out.append("\n**Watch-outs**"); out.append("- None flagged by the current checks — keep monitoring collections, stock, and readiness.")
    if ins.get("actions"):
        out.append("\n**Suggested actions (data-informed)**"); out += [f"- {x}" for x in ins["actions"]]
    return "\n".join(out)

def _fmt_report(data: dict) -> str:
    if data.get("error"):
        return f"I couldn’t generate that report ({data.get('error')}). Want to try a different report or date range?"
    kind = data.get("kind") or "report"
    report = data.get("report")
    label = data.get("date") or f"{data.get('start','')} → {data.get('end','')}"
    title = kind.replace("_"," ").upper()
    lines = [f"Here’s the **{title}** report for **{label}** (grounded in real records):"]
    if isinstance(report, dict):
        # show key metrics first
        keys_order = ["revenue","total_revenue","net_revenue","expenses","total_expenses","net_profit","net_income","net","cash_in","cash_out","net_cash","vat_collected","vat_paid","net_vat","occupancy_pct","adr","revpar","room_nights","rooms_sold","rooms_available","count","total"]
        shown = set()
        metrics = []
        for k in keys_order:
            if k in report and isinstance(report[k], (int,float,str)) and not isinstance(report[k], bool):
                metrics.append((k, report[k])); shown.add(k)
        if metrics:
            for k,v in metrics[:12]:
                name = k.replace('_',' ').title()
                if isinstance(v,(int,float)) and any(x in k for x in ["revenue","expense","profit","income","net","cash","vat","adr","revpar","total"]):
                    lines.append(f"- {name}: **{_fmt_money(v)}**")
                else:
                    lines.append(f"- {name}: **{v}**")
        # include other scalar/string fields
        for k,v in report.items():
            if k in shown: continue
            if isinstance(v, (int,float,str)) and not isinstance(v,bool) and len(lines) < 22:
                lines.append(f"- {k.replace('_',' ').title()}: **{_fmt_money(v) if isinstance(v,(int,float)) and 'pct' not in k and 'count' not in k else v}**")
            elif isinstance(v, list) and v and len(lines) < 22:
                lines.append(f"- {k.replace('_',' ').title()}: {len(v)} item(s)")
        if "text" in report and isinstance(report["text"], str):
            lines.append("\nDetails:\n" + "\n".join(report["text"].strip().splitlines()[:20]))
    elif isinstance(report, str):
        excerpt = "\n".join(report.strip().splitlines()[:25])
        lines.append(excerpt if excerpt else "- (empty report)")
    else:
        lines.append(_format_data("Report data", report))
    return "\n".join(lines)

def _fmt_occupancy(data: dict) -> str:
    if data.get("error"):
        return f"I couldn’t load occupancy ({data.get('error')})."
    occ = data.get("occupancy") or {}
    start, end = data.get("start"), data.get("end")
    if not occ:
        return f"No occupancy data found for **{start} → {end}**."
    lines = [f"Occupancy summary for **{start} → {end}** (real data):"]
    for k,label in [("pct","Occupancy"),("occupancy_pct","Occupancy"),("adr","ADR"),("revpar","RevPAR"),("room_nights","Room nights"),("rooms_sold","Rooms sold"),("rooms_available","Rooms available"),("revenue","Revenue"),("total_revenue","Revenue")]:
        if k in occ and occ[k] is not None and label+"**" not in "\n".join(lines):
            v = occ[k]
            if isinstance(v,(int,float)) and k not in ("room_nights","rooms_sold","rooms_available") and "pct" not in k:
                lines.append(f"- {label}: **{_fmt_money(v)}**" if "pct" not in k else f"- {label}: **{v}%**")
            else:
                lines.append(f"- {label}: **{v}{'%' if 'pct' in k else ''}**")
    # include any other scalars
    for k,v in occ.items():
        if isinstance(v,(int,float,str)) and k not in ("pct","occupancy_pct","adr","revpar","room_nights","rooms_sold","rooms_available","revenue","total_revenue") and len(lines)<18:
            nm=k.replace('_',' ').title()
            lines.append(f"- {nm}: **{v}**")
    return "\n".join(lines)

def _fmt_compare(data: dict) -> str:
    cur = data.get("current") or {}; prev = data.get("previous") or {}
    lines = [f"Here’s a grounded comparison: **{cur.get('start','?')} → {cur.get('end','?')}** vs **{prev.get('start','?')} → {prev.get('end','?')}**."]
    def pl_net(x):
        pl = x.get("pl") or {}
        if isinstance(pl, dict):
            rev = float(pl.get("revenue", pl.get("total_revenue", pl.get("net_revenue", 0))) or 0)
            exp = float(pl.get("expenses", pl.get("total_expenses", 0)) or 0)
            net = float(pl.get("net_profit", pl.get("net_income", rev-exp)) or (rev-exp))
            return rev, exp, net
        return None
    cpl, ppl = pl_net(cur), pl_net(prev)
    if cpl and ppl:
        lines.append(f"- **P&L:** net {_fmt_money(cpl[2])} (rev {_fmt_money(cpl[0])}, exp {_fmt_money(cpl[1])}) vs {_fmt_money(ppl[2])} previously (Δ {_fmt_money(cpl[2]-ppl[2])}).")
    cocc = (cur.get("occupancy") or {}); pocc = (prev.get("occupancy") or {})
    if cocc or pocc:
        cp = float(cocc.get("pct", cocc.get("occupancy_pct", 0)) or 0); pp = float(pocc.get("pct", pocc.get("occupancy_pct", 0)) or 0)
        lines.append(f"- **Occupancy:** {cp}% vs {pp}% (Δ {round(cp-pp,2)} pp), ADR {_fmt_money(cocc.get('adr',0))} vs {_fmt_money(pocc.get('adr',0))}, RevPAR {_fmt_money(cocc.get('revpar',0))} vs {_fmt_money(pocc.get('revpar',0))}.")
    ccf = cur.get("cash_flow"); pcf = prev.get("cash_flow")
    if ccf or pcf:
        def nc(cf):
            if isinstance(cf, dict):
                for k in ("net_cash","net","cash_net"):
                    if k in cf: return float(cf.get(k) or 0)
            return None
        a,b = nc(ccf), nc(pcf)
        if a is not None or b is not None:
            lines.append(f"- **Cash flow (net):** {_fmt_money(a or 0)} vs {_fmt_money(b or 0)}.")
    if len(lines) == 1:
        lines.append("- Detailed period data was retrieved; ask for a specific report (P&L, occupancy, or cash flow) if you want the full breakdown.")
    return "\n".join(lines)

def _fmt_outstanding(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load outstanding balances ({data.get('error')})."
    count = int(data.get("count") or 0); total = float(data.get("total_due") or 0)
    items = data.get("outstanding") or []
    if count == 0:
        return "Good news — there are **no outstanding balances** on active/checked-out reservations right now (based on real records)."
    lines = [f"Here’s who currently owes money (real data): **{count}** reservation(s), **{_fmt_money(total)}** total due."]
    for r in items[:15]:
        lines.append(f"- Res #{r.get('reservation_id')} — {r.get('guest_name','Guest')} (room {r.get('room') or '-'}, {r.get('status','')}, {r.get('check_in','')}→{r.get('check_out','')}): **{_fmt_money(r.get('balance_due',0))}** due")
    if count > 15:
        lines.append(f"- …and {count-15} more. Ask if you want the full list or the largest balances.")
    return "\n".join(lines)

def _fmt_inventory_alerts(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load inventory alerts ({data.get('error')})."
    a = (data or {}).get("alerts") or {}
    neg = a.get("negative") or []; low = a.get("low") or []; over = a.get("overstock") or []
    if not neg and not low and not over:
        return "Inventory looks healthy — **no low, negative, or overstock alerts** right now (real data)."
    lines = ["Here are the current inventory alerts (real data):"]
    if neg:
        lines.append(f"- **Negative stock ({len(neg)}):** " + ", ".join([f"{i.get('item_name')} (#{i.get('id')}, qty {i.get('stock_qty')})" for i in neg[:8]]))
    if low:
        lines.append(f"- **Low stock ({len(low)}):** " + ", ".join([f"{i.get('item_name')} (#{i.get('id')}, qty {i.get('stock_qty')}, thr {i.get('threshold')})" for i in low[:8]]))
    if over:
        lines.append(f"- **Overstock ({len(over)}):** " + ", ".join([f"{i.get('item_name')} (#{i.get('id')}, qty {i.get('stock_qty')}, thr {i.get('threshold')})" for i in over[:8]]))
    return "\n".join(lines)

def _fmt_top_sellers(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load top sellers ({data.get('error')})."
    items = data.get("top_items") or []
    start, end = data.get("start"), data.get("end")
    if not items:
        return f"No non-voided POS sales found for **{start} → {end}**. Want to try a different period?"
    lines = [f"Top selling items for **{start} → {end}** (grounded in POS data):"]
    for i,it in enumerate(items[:10],1):
        lines.append(f"{i}. **{it.get('item')}** — {it.get('qty_sold')} sold, {_fmt_money(it.get('gross',0))} gross ({it.get('lines',0)} lines)")
    return "\n".join(lines)

def _fmt_expenses(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load expenses ({data.get('error')})."
    cats = data.get("by_category") or []; total = float(data.get("total") or 0)
    start, end = data.get("start"), data.get("end")
    if not cats:
        return f"No expenses recorded for **{start} → {end}**."
    lines = [f"Expenses summary for **{start} → {end}** (real data): **{_fmt_money(total)}** total."]
    for c in cats[:12]:
        lines.append(f"- {c.get('category')}: **{_fmt_money(c.get('total',0))}** (net {_fmt_money(c.get('net',0))}, VAT {_fmt_money(c.get('vat',0))}, {c.get('count',0)} entries)")
    if len(cats) > 12: lines.append(f"- …and {len(cats)-12} more categories.")
    return "\n".join(lines)

def _fmt_sales_breakdown(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load sales breakdown ({data.get('error')})."
    start,end = data.get("start"), data.get("end")
    lines = [f"Sales & collections breakdown for **{start} → {end}** (real data):"]
    pos = data.get("pos_by_item") or []
    if pos:
        lines.append("**POS by item (top):**")
        for i in pos[:10]: lines.append(f"- {i.get('item')}: {i.get('qty')} sold, {_fmt_money(i.get('gross',0))} gross")
    pays = data.get("payments_by_method") or []
    if pays:
        lines.append("**Payments by method:**")
        for p in pays: lines.append(f"- {p.get('method')}: {_fmt_money(p.get('total',0))} ({p.get('count',0)} txns)")
    src = data.get("reservations_by_source") or []
    if src:
        lines.append("**Reservations by source:** " + ", ".join([f"{s.get('source')}: {s.get('count')}" for s in src[:8]]))
    if len(lines) == 1: lines.append("- No sales/payment/source data found for this period.")
    return "\n".join(lines)

def _fmt_pipeline(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load the reservation pipeline ({data.get('error')})."
    by = data.get("by_status") or {}
    upcoming = data.get("upcoming_arrivals_7d") or []
    lines = ["Reservation pipeline (real data):"]
    if by:
        lines.append("- **By status:** " + ", ".join([f"{k}: {v}" for k,v in by.items()]))
    if upcoming:
        lines.append(f"- **Upcoming arrivals (next 7 days): {len(upcoming)}**")
        for r in upcoming[:10]: lines.append(f"  - Res #{r.get('id')} {r.get('guest_name','')} — room {r.get('room_number') or '-'}, {r.get('check_in_date')}→{r.get('check_out_date')} ({r.get('status')})")
        if len(upcoming)>10: lines.append(f"  - …and {len(upcoming)-10} more.")
    else:
        lines.append("- No upcoming arrivals in the next 7 days.")
    return "\n".join(lines)

def _fmt_housekeeping(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load housekeeping summary ({data.get('error')})."
    s = data.get("summary") or {}
    by = s.get("by_status") or {}
    total = s.get("total_rooms", sum(by.values()) if by else 0)
    if not by:
        return "No housekeeping data available right now."
    parts = [f"{k}: **{v}**" for k,v in by.items()]
    return f"Housekeeping status (real data, {total} rooms): " + ", ".join(parts) + "."

def _fmt_availability(data: dict) -> str:
    if data.get("error"): return f"I couldn’t check availability ({data.get('error')})."
    start,end = data.get("start"), data.get("end")
    rooms = data.get("rooms") or []; count = data.get("available_count", len(rooms))
    if count == 0:
        return f"No rooms are available for **{start} → {end}** (based on real availability). Want to try different dates?"
    lines = [f"Room availability for **{start} → {end}**: **{count}** room(s) available."]
    for r in rooms[:15]:
        lines.append(f"- {r.get('room_number')} ({r.get('room_type','')}) — base {_fmt_money(r.get('base_price',0))}, housekeeping: {r.get('housekeeping_status','')}")
    if count > 15: lines.append(f"- …and {count-15} more.")
    return "\n".join(lines)

def _fmt_menu(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load the menu ({data.get('error')})."
    meals = data.get("meals") or []
    if not meals: return "No meals/menu items are registered yet."
    lines = [f"Menu / meals (real data, {data.get('count', len(meals))} items):"]
    for m in meals[:20]:
        name = m.get("name") or m.get("item_name") or f"Meal #{m.get('id')}"
        price = m.get("selling_price", m.get("price"))
        active = m.get("active")
        extra = f" — {_fmt_money(price)}" if price is not None else ""
        if active is not None: extra += f" ({'active' if active else 'inactive'})"
        desc = _short(m.get("description") or "", 80)
        lines.append(f"- **{name}**{extra}" + (f": {desc}" if desc else ""))
    if len(meals)>20: lines.append(f"- …and {len(meals)-20} more.")
    return "\n".join(lines)

def _fmt_rate_plans(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load rate plans ({data.get('error')})."
    plans = data.get("rate_plans") or []
    if not plans: return "No rate plans found."
    lines = [f"Rate plans (real data, {len(plans)}):"]
    for p in plans[:30]:
        name = p.get("name") or f"Plan #{p.get('id')}"
        mod = p.get("price_modifier", p.get("modifier", 0))
        used = p.get("reservations_using_plan")
        used_s = f", used by {used} reservation(s)" if used is not None else ""
        lines.append(f"- **{name}** (#{p.get('id')}): modifier {mod}{used_s}")
    return "\n".join(lines)

def _fmt_health_diagnostics(data: dict) -> str:
    if data.get("error"): return f"I couldn’t collect health/doctor diagnostics ({data.get('error')})."
    lines = [f"Doctor/health diagnostics ({'OK' if data.get('ok') else 'issues detected'}, real local data):"]
    sch = data.get("schema") or {}
    if sch:
        lines.append(f"- Schema: version {sch.get('user_version')} (expected {sch.get('expected_version')}), matches={sch.get('matches_expected')}")
        t = sch.get("tables") or {}
        if t: lines.append(f"  - Tables: ok={t.get('ok')} present={t.get('present_count')} missing={len(t.get('missing_tables') or [])}")
    integ = (data.get("integrity") or {}).get("sqlite") or {}
    if integ:
        lines.append(f"- SQLite integrity: {integ.get('integrity_check')} | FK violations: {len(integ.get('foreign_key_violations') or [])}")
    ledger = (data.get("integrity") or {}).get("ledger")
    if ledger is not None:
        lines.append(f"- Ledger integrity: {ledger}")
    cons = (data.get("consistency") or {}).get("batch_stock") or {}
    if cons:
        lines.append(f"- Batch/stock consistency: ok={cons.get('ok')} checked={cons.get('checked')} mismatches={cons.get('mismatch_count', 0)}")
        for m in (cons.get("mismatches") or [])[:3]:
            lines.append(f"  - item #{m.get('item_id')} {m.get('item_name')}: stock {m.get('stock_qty')} vs active batch remaining {m.get('batch_remaining_active')}")
    pr = (data.get("readiness") or {}).get("printer") or {}
    if pr:
        lines.append(f"- Printer readiness: configured={pr.get('configured')} mode={pr.get('mode','')} target_set={pr.get('target_set', False)}")
    br = data.get("backup_restore") or {}
    if br:
        rd = br.get("readiness") or {}
        lines.append(f"- Backup/restore readiness: db_exists={rd.get('db_exists')} data_dir_writable={rd.get('data_dir_writable')}")
        b = br.get("backup") or {}
        if b:
            lines.append(f"  - Backup helper: {b.get('cli_helper')} (stop app recommended={b.get('stop_app_first')})")
        r = br.get("restore") or {}
        if r:
            lines.append(f"  - Restore verify helper: {r.get('verify_helper')}")
    upd = data.get("update") or {}
    if isinstance(upd, dict) and (upd.get("app_version") or upd.get("commit_short") or upd.get("branch")):
        lines.append(f"- App/update (local): version={upd.get('app_version') or '-'} commit={upd.get('commit_short') or '-'} branch={upd.get('branch') or '-'} dirty={upd.get('dirty')}")
    issues = data.get("config_issues") or []
    if issues:
        lines.append(f"- Config issues: {len(issues)}")
        for iss in issues[:5]:
            lines.append(f"  - [{iss.get('severity','info')}] {iss.get('code')}: {iss.get('message')}")
    lines.append("Diagnostics are read-only and grounded in local data; runtime data (DB/exports/invoices) is preserved by doctor/update/backup guidance.")
    return "\n".join(lines)


def _fmt_integrity(data: dict) -> str:
    if data.get("error"): return f"I couldn’t run the integrity/health check ({data.get('error')})."
    if "backup_restore" in data or "consistency" in data or "readiness" in data:
        return _fmt_health_diagnostics(data)
    ok = data.get("ok")
    if ok:
        return "Ledger/data integrity check: **OK** — no issues reported by the current read-only checks (real data)."
    issues = data.get("issues") or data.get("errors") or data.get("problems")
    lines = ["Ledger/data integrity check: **issues found** (real data)."]
    if isinstance(issues, list) and issues:
        for it in issues[:12]: lines.append(f"- {it}")
    elif issues:
        lines.append(f"- {issues}")
    else:
        for k,v in data.items():
            if k=="ok": continue
            if v and isinstance(v,(list,dict,str)) and len(lines)<12:
                lines.append(f"- {k}: {_short(json.dumps(v, ensure_ascii=False, default=str) if not isinstance(v,str) else v, 140)}")
    lines.append("Ask for a `doctor report` for full schema/batch-stock/printer/backup-readiness diagnostics (read-only).")
    return "\n".join(lines)

def _fmt_recent_activity(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load recent activity ({data.get('error')})."
    rows = data.get("recent_audit") or []
    if not rows: return "No recent audit/activity entries found."
    lines = [f"Recent activity (latest {len(rows)} audit entries, real data):"]
    for r in rows[:20]:
        when = r.get("at") or r.get("created_at") or ""
        action = r.get("action") or r.get("event") or r.get("type") or ""
        detail = r.get("detail") or r.get("description") or r.get("entity") or ""
        who = r.get("user") or r.get("actor") or ""
        parts = [p for p in [str(when).strip(), str(action).strip(), str(detail).strip(), str(who).strip()] if p]
        lines.append("- " + " | ".join(parts) if parts else f"- { _short(json.dumps(r, ensure_ascii=False, default=str), 160)}")
    if len(rows)>20: lines.append(f"- …and {len(rows)-20} more.")
    return "\n".join(lines)

def _fmt_activities_catalog(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load activities ({data.get('error')})."
    items = data.get("activities") or []
    if not items: return "No activities/trips are registered yet. You can register them in **POS / Inventory → Activities / Trips**."
    lines = [f"Registered activities/trips catalog (real data, {data.get('count', len(items))}):"]
    for a in items[:25]:
        status = "active" if a.get("active") else "inactive"
        lines.append(f"- **{a.get('name')}** (#{a.get('id')}, {a.get('category','Trip')}, {_fmt_money(a.get('unit_price',0))}, {status})" + (f": {_short(a.get('description',''),80)}" if a.get('description') else ""))
    if len(items)>25: lines.append(f"- …and {len(items)-25} more.")
    lines.append("\nBookings for these activities are posted to the reservation folio and invoice (like POS).")
    return "\n".join(lines)

def _fmt_activity_stats(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load activity stats ({data.get('error')})."
    stats = data.get("activity_stats") or []
    if not stats: return "No activity/trip statistics available yet."
    lines = ["Activity/trip booking statistics (grounded in real bookings):"]
    for s in stats[:20]:
        lines.append(f"- **{s.get('activity_name')}** (#{s.get('activity_id')}, {s.get('category','')}): {s.get('bookings',0)} booking(s), qty {s.get('total_qty',0)}, revenue {_fmt_money(s.get('gross_revenue',0))}" + (f" — last booked {s.get('last_booked')}" if s.get('last_booked') else ""))
    if len(stats)>20: lines.append(f"- …and {len(stats)-20} more.")
    return "\n".join(lines)

def _fmt_activity_bookings(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load activity bookings ({data.get('error')})."
    bookings = data.get("bookings") or []
    if not bookings: return "No booked activities/trips found for the selected filter (real data)."
    lines = [f"Booked activities/trips ({data.get('count', len(bookings))} found, grounded in real bookings):"]
    for b in bookings[:20]:
        who = b.get("guest_name") or (f"guest #{b.get('guest_id')}" if b.get("guest_id") else "guest")
        lines.append(f"- **{b.get('activity_name') or b.get('item_description')}** x{b.get('quantity',1)} ({_fmt_money(b.get('unit_price',0))} each) — Res #{b.get('reservation_id')} {who}, room {b.get('room_number') or '-'}, {b.get('check_in_date','')}→{b.get('check_out_date','')} (booked {b.get('created_at','')})")
    if len(bookings)>20: lines.append(f"- …and {len(bookings)-20} more.")
    return "\n".join(lines)

def _fmt_reservation(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load the reservation ({data.get('error')})."
    rid = data.get("reservation_id") or data.get("id")
    # get_guest_balance shape
    if "breakdown" in data or ("balance" in data and "reservation_id" in data):
        bd = data.get("breakdown") or {}
        bal = data.get("balance", bd.get("balance_due", 0))
        lines = [f"Reservation #{rid} — balance (real data):"]
        lines.append(f"- **Balance due:** {_fmt_money(bal)}")
        if bd:
            for k,label in [("total_charges","Total charges"),("accommodation_gross","Accommodation"),("pos_gross","POS/activities"),("total_paid","Total paid")]:
                if bd.get(k) is not None: lines.append(f"- {label}: {_fmt_money(bd.get(k))}")
        return "\n".join(lines)
    # summary shape
    lines = [f"Reservation #{rid} (real data):"]
    for k,label in [("guest_name","Guest"),("guest_id","Guest ID"),("status","Status"),("room_number","Room"),("room_type","Room type"),("check_in_date","Check-in"),("check_out_date","Check-out"),("nights","Nights"),("source","Source"),("rate_plan","Rate plan")]:
        if data.get(k) is not None: lines.append(f"- {label}: {data.get(k)}")
    for k,label in [("agreed_nightly_rate","Nightly rate"),("tax_amount","Tax")]:
        if data.get(k) is not None: lines.append(f"- {label}: {_fmt_money(data.get(k))}")
    bal = data.get("balance") or data.get("balance_due")
    if bal is not None: lines.append(f"- **Balance due:** {_fmt_money(bal)}")
    if data.get("pos_count") is not None: lines.append(f"- POS/activity items: {data.get('pos_count')}")
    if data.get("notes"): lines.append(f"- Notes: {_short(data.get('notes'), 140)}")
    return "\n".join(lines)

def _fmt_guest_search(data: dict) -> str:
    if data.get("error"): return f"I couldn’t search guests ({data.get('error')})."
    q = data.get("query") or ""; guests = data.get("guests") or []
    if not guests:
        return f"I couldn’t find any guests matching “{q}” in the real records. Try a different name/phone/email fragment or a guest ID."
    lines = [f"I found **{len(guests)}** guest match(es) for “{q}” (real data):"]
    for g in guests[:10]:
        name = g.get("name") or g.get("canonical_name") or "Guest"
        lines.append(f"- #{g.get('id')} **{name}** — phone: {g.get('phone') or '-'}, email: {g.get('email') or '-'}, stays: {g.get('stays', g.get('reservation_count', 0))}, last stay: {g.get('last_stay') or '-'}")
    if len(guests)>10: lines.append(f"- …and {len(guests)-10} more.")
    lines.append("Tell me the guest ID (e.g. `guest #12`) if you want their full portfolio, last visit, or bookings.")
    return "\n".join(lines)

def _fmt_guest_profile(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load that guest profile ({data.get('error')})."
    g = data.get("guest") or {}
    if not g and data.get("id"): g = data
    gid = g.get("id") or g.get("guest_id")
    name = g.get("name") or g.get("canonical_name") or f"Guest #{gid}"
    lines = [f"Here’s the profile for **{name}** (#{gid}, real data):"]
    lines.append(f"- Phone: {g.get('phone') or '-'} | Email: {g.get('email') or '-'}")
    if g.get("notes"): lines.append(f"- Notes: {_short(g.get('notes'), 160)}")
    recents = data.get("recent_reservations") or data.get("reservations") or []
    if recents:
        lines.append(f"- Recent reservations ({min(len(recents),5)} shown):")
        for r in recents[:5]:
            lines.append(f"  - Res #{r.get('id')} room {r.get('room_number') or r.get('room_id') or '-'} {r.get('check_in_date','')}→{r.get('check_out_date','')} ({r.get('status','')})")
    else:
        lines.append("- No reservations on file.")
    return "\n".join(lines)

def _fmt_guest_last_visit(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load the last visit ({data.get('error')})."
    if not data.get("found") and not data.get("last_visit"):
        gid = data.get("guest_id") or "this guest"
        return f"I couldn’t find a last visit for guest #{gid} in the real records (no eligible reservations)."
    lv = data.get("last_visit") or data
    gid = data.get("guest_id") or lv.get("guest_id")
    basis = data.get("basis") or ("most recent Checked Out stay" if lv.get("status") == "Checked Out" else "most recent non-deleted reservation")
    lines = [f"Last visit for guest #{gid} (based on {basis}, real data):"]
    rid = lv.get("reservation_id") or lv.get("id")
    if rid: lines.append(f"- Reservation: #{rid} ({lv.get('status','')})")
    if lv.get("room_number") or lv.get("room_id"): lines.append(f"- Room: {lv.get('room_number') or lv.get('room_id')} {('('+lv.get('room_type')+')') if lv.get('room_type') else ''}".strip())
    if lv.get("check_in_date") or lv.get("check_out_date"): lines.append(f"- Dates: {lv.get('check_in_date','')} → {lv.get('check_out_date','')}" + (f" ({lv.get('nights')} nights)" if lv.get('nights') else ""))
    if lv.get("agreed_nightly_rate") is not None: lines.append(f"- Nightly rate: {_fmt_money(lv.get('agreed_nightly_rate'))}")
    if lv.get("source"): lines.append(f"- Source: {lv.get('source')}")
    return "\n".join(lines)

def _fmt_guest_top_items(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load top ordered items ({data.get('error')})."
    gid = data.get("guest_id")
    if not data.get("found") and not data.get("items"):
        rid = (data.get("last_visit") or {}).get("reservation_id") or (data.get("last_visit") or {}).get("id")
        if rid: return f"Guest #{gid}’s last visit (Res #{rid}) has no non-voided POS/activity orders recorded."
        return f"I couldn’t determine last-visit orders for guest #{gid} (no eligible last visit found)."
    lv = data.get("last_visit") or {}
    items = data.get("items") or []
    rid = lv.get("reservation_id") or lv.get("id")
    lines = [f"Most ordered items on guest #{gid}’s last visit (Res #{rid}, real POS data):"]
    if not items:
        lines.append("- No non-voided items/orders recorded for that stay.")
    else:
        for i,it in enumerate(items[:10],1):
            lines.append(f"{i}. **{it.get('item_description') or it.get('item')}** — qty {it.get('total_qty', it.get('qty', it.get('quantity',0)))}" + (f", spend {_fmt_money(it.get('gross_spend', it.get('gross',0)))}" if it.get('gross_spend') is not None or it.get('gross') is not None else ""))
    return "\n".join(lines)

def _fmt_guest_last_visit_spend(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load last-visit spend ({data.get('error')})."
    gid = data.get("guest_id")
    lv = data.get("last_visit") or {}
    rid = lv.get("reservation_id") or lv.get("id")
    lines = [f"Last visit spend for guest #{gid} (real data):"]
    if rid: lines.append(f"- Stay: Res #{rid} {lv.get('check_in_date','')}→{lv.get('check_out_date','')} ({lv.get('status','')}) room {lv.get('room_number') or '-'}")
    bd = data.get("balance_breakdown") or {}
    if bd:
        for k,label in [("total_charges","Total charges"),("accommodation_gross","Accommodation"),("pos_gross","POS/activities"),("total_paid","Total paid"),("balance_due","Balance due")]:
            if bd.get(k) is not None:
                prefix = "**" if k=="balance_due" else ""
                lines.append(f"- {label}: {prefix}{_fmt_money(bd.get(k))}{prefix}")
    elif data.get("spend") is not None:
        lines.append(f"- Spend/balance: {_fmt_money(data.get('spend'))}")
    items = data.get("top_items") or []
    if items:
        lines.append("- Top items on that stay: " + ", ".join([f"{it.get('item_description') or it.get('item')} (x{it.get('total_qty', it.get('qty',0))})" for it in items[:5]]))
    return "\n".join(lines)

def _fmt_guest_portfolio(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load the guest portfolio ({data.get('error')})."
    g = data.get("guest") or {}; s = data.get("stats") or {}
    gid = g.get("id") or g.get("guest_id"); name = g.get("name") or g.get("canonical_name") or f"Guest #{gid}"
    lines = [f"Guest portfolio (360°) for **{name}** (#{gid}) — grounded in real data:"]
    lines.append(f"- Contact: phone {g.get('phone') or '-'}, email {g.get('email') or '-'}")
    lines.append(f"- Stays: **{s.get('stays_total',0)}** total ({s.get('completed_stays',0)} completed, {s.get('total_nights',0)} nights) | first: {s.get('first_visit') or '-'} | last: {s.get('last_visit') or '-'} | favorite room: {s.get('favorite_room') or '-'}")
    lines.append(f"- Financials: charged **{_fmt_money(s.get('total_charges',0))}**, paid **{_fmt_money(s.get('total_paid',0))}**, outstanding **{_fmt_money(s.get('total_outstanding',0))}**, POS/activities **{_fmt_money(s.get('pos_gross_total',0))}**")
    trips = data.get("trips") or []
    if trips:
        lines.append(f"\n**Trips / reservations ({len(trips)}):**")
        for t in trips[:8]:
            bd = t.get("balance") or {}
            invc = t.get("invoice_files") or t.get("invoices") or []
            invn = len(invc) if isinstance(invc, list) else 0
            lines.append(f"- Res #{t.get('id')} room {t.get('room_number') or '-'} {t.get('check_in_date','')}→{t.get('check_out_date','')} ({t.get('status','')}): charges {_fmt_money(bd.get('total_charges',0))}, paid {_fmt_money(bd.get('total_paid',0))}, due {_fmt_money(bd.get('balance_due',0))}, POS items {t.get('pos_active_count', t.get('pos_count',0))}, invoices {invn}")
        if len(trips)>8: lines.append(f"- …and {len(trips)-8} more stays (open the guest portfolio in the app for full per-stay detail).")
    else:
        lines.append("- No trips/reservations on file.")
    top = data.get("top_items_overall") or []
    if top:
        lines.append("**Top items overall:** " + ", ".join([f"{i.get('item')} ({_fmt_money(i.get('gross',0))})" for i in top[:6]]))
    posn = data.get("all_pos_count", 0); payn = data.get("all_payments_count", 0)
    lines.append(f"\nTotals on record: {posn} POS/activity line(s), {payn} payment(s). All figures above are from real backend data.")
    return "\n".join(lines)

def _fmt_decision_support(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load decision support ({data.get('error')})."
    start,end = data.get("start"), data.get("end")
    lines = [f"Decision support for **{start} → {end}** (grounded in real data):"]
    ds = data.get("decision_support")
    fs = data.get("financial_summary")
    if isinstance(ds, dict):
        # highlights
        for k in ("kpis","signals","strengths","risks","recommendations","summary"):
            v = ds.get(k)
            if v is None: continue
            if isinstance(v, list) and v:
                lines.append(f"**{k.replace('_',' ').title()}:**")
                for it in v[:8]: lines.append(f"- {it if isinstance(it,str) else _short(json.dumps(it,ensure_ascii=False,default=str),140)}")
            elif isinstance(v, dict) and v:
                lines.append(f"**{k.replace('_',' ').title()}:**")
                for kk,vv in list(v.items())[:8]: lines.append(f"- {kk}: {vv}")
            elif isinstance(v, str) and v.strip():
                lines.append(f"- {k.replace('_',' ').title()}: {v.strip()}")
        # scalar kpis
        for k,v in ds.items():
            if k in ("kpis","signals","strengths","risks","recommendations","summary"): continue
            if isinstance(v,(int,float,str)) and len(lines)<22:
                lines.append(f"- {k.replace('_',' ').title()}: **{_fmt_money(v) if isinstance(v,(int,float)) and 'pct' not in k else v}**")
    elif isinstance(ds, str) and ds.strip():
        lines.append("\n".join(ds.strip().splitlines()[:20]))
    if isinstance(fs, dict) and fs:
        lines.append("**Financial summary:**")
        for k,v in list(fs.items())[:10]:
            if isinstance(v,(int,float,str)):
                lines.append(f"- {k.replace('_',' ').title()}: **{_fmt_money(v) if isinstance(v,(int,float)) and 'pct' not in k else v}**")
    if len(lines)==1: lines.append(_format_data("Decision support data", data))
    return "\n".join(lines)

def _proactive_suggestions(intent: str, data: dict, answer_text: str = "") -> list[str]:
    sug = []
    try:
        if intent == "outstanding_balances":
            if (data or {}).get("count",0) > 0: sug.append("Want the full list sorted by largest balance, or a specific reservation’s folio?")
            sug.append("I can also run a business partner briefing for this month.")
        elif intent == "inventory_alerts":
            a = (data or {}).get("alerts") or {}
            if (a.get("low") or a.get("negative")): sug.append("Want to see top-selling items to prioritize what to restock?")
        elif intent == "property_stats":
            sug.append("Ask for “today’s ops” or “business partner briefing for this month” for more detail.")
        elif intent == "today_ops":
            sug.append("Want housekeeping details, today’s arrivals/departures list, or who owes money?")
        elif intent in ("report_pl","report_cash_flow","report_vat","report_payment_methods","report_occupancy","occupancy_summary"):
            sug.append("I can compare this period to the previous one if you’d like.")
        elif intent == "compare_periods":
            sug.append("Want a business partner briefing or a deeper P&L/cash-flow breakdown for the current period?")
        elif intent in ("guest_profile","guest_last_visit","guest_top_items_last_visit","guest_last_visit_spend"):
            gid = (data or {}).get("guest_id") or (((data or {}).get("guest") or {}).get("id"))
            if gid: sug.append(f"Want the full 360° portfolio for guest #{gid} (all stays, POS, payments, invoices)?")
            sug.append("I can also show their booked activities/trips.")
        elif intent == "guest_portfolio":
            gid = ((data or {}).get("guest") or {}).get("id") or (data or {}).get("guest_id")
            outst = (((data or {}).get("stats") or {}).get("total_outstanding") or 0)
            if outst and outst > 0: sug.append("This guest has an outstanding balance — want details on which reservation is due?")
            if gid: sug.append(f"Want to see booked activities/trips for guest #{gid}?")
        elif intent == "guest_search":
            if (data or {}).get("guests"): sug.append("Tell me the guest ID to open their full portfolio or last visit.")
        elif intent == "reservation_summary":
            rid = (data or {}).get("reservation_id") or (data or {}).get("id")
            bal = (data or {}).get("balance_due") or (data or {}).get("balance")
            if bal and float(bal) > 0: sug.append(f"Reservation #{rid} has a balance due — want the folio breakdown?")
        elif intent == "reservation_balance":
            sug.append("Want the full reservation summary as well?")
        elif intent == "top_selling_items":
            sug.append("Want to check inventory alerts to make sure top sellers are stocked?")
        elif intent == "list_activities":
            sug.append("Want activity/trip booking statistics or bookings for a specific guest?")
        elif intent == "activity_stats":
            sug.append("Ask for bookings of a specific activity or a guest’s booked activities.")
        elif intent == "business_partner_briefing":
            sug.append("Want me to drill into any watch-out (AR, inventory, occupancy, or expenses)?")
        elif intent == "housekeeping_summary":
            sug.append("Want today’s ops snapshot or arrivals to prioritize readiness?")
        elif intent == "capabilities_help":
            sug.append("Ask me anything above — I’ll answer from real app data.")
    except Exception:
        logger.debug("assistant._proactive_suggestions: failed building primary suggestions intent=%r", intent, exc_info=True)
    # batch/expiry/trace, integrity/health, updates
    try:
        if intent in ("expiring_batches", "get_expiring_batches"):
            n = (data or {}).get("count", 0)
            if n: sug.append("Want to trace a specific lot/barcode to see received date, expiry, and movements?")
            sug.append("I can also list all inventory batches/lots or show inventory alerts.")
        elif intent in ("inventory_batches", "list_inventory_batches"):
            sug.append("Want to trace a barcode/SKU/lot code for full traceability (received/expiry/movements)?")
            sug.append("I can also show batches expiring soon.")
        elif intent in ("trace_barcode", "barcode_trace"):
            sug.append("Want to list all batches for this item or see which batches are expiring soon?")
        elif intent in ("ledger_integrity", "verify_ledger_integrity"):
            ok = (data or {}).get("ok")
            if ok is False: sug.append("Consider reviewing the flagged items in-app (Settings/health checks) before relying on reports.")
            else: sug.append("Want today’s ops or a business partner briefing for a quick health/ops snapshot?")
        elif intent in ("app_update_status", "get_app_update_status", "app_version"):
            sug.append("For git-based installs you can check/apply updates in Settings → App updates (changelog shown; data preserved; restart after apply).")
    except Exception:
        logger.debug("assistant._proactive_suggestions: failed building secondary suggestions intent=%r", intent, exc_info=True)
    # dedupe, keep max 2
    out=[]; seen=set()
    for s in sug:
        if s in seen: continue
        seen.add(s); out.append(s)
    return out[:2]

def _fmt_expiring_batches(data: dict) -> str:
    if data.get("error"):
        return f"I couldn’t load expiring batches ({data.get('error')})."
    batches = data.get("batches") or []
    days = data.get("within_days")
    lines = [f"Inventory batches expiring/expired{f' within {days} day(s)' if days is not None else ''} (real, traceable lots):"]
    if not batches:
        lines.append("- None found. Stock looks clear for the selected window.")
        return "\n".join(lines)
    for b in batches[:25]:
        st = b.get('expiry_state') or b.get('status') or ''
        lines.append(f"- **{b.get('item_name') or ('Item #'+str(b.get('item_id')))}** lot `{b.get('lot_code')}`: remaining **{b.get('quantity_remaining')}** (recv {b.get('quantity_received')}), received {b.get('received_at') or '-'}, expiry {b.get('expiry_date') or '-'} [{st}], cost {_fmt_money(b.get('unit_cost',0))}, supplier {b.get('supplier') or '-'}")
    if len(batches) > 25:
        lines.append(f"- …and {len(batches)-25} more batches.")
    return "\n".join(lines)

def _fmt_inventory_batches(data: dict) -> str:
    if data.get("error"):
        return f"I couldn’t load inventory batches ({data.get('error')})."
    batches = data.get("batches") or []
    item_id = data.get("item_id")
    lines = [f"Inventory batches/lots{f' for item #{item_id}' if item_id else ''} (traceable, real data):"]
    if not batches:
        lines.append("- No batches found for the given criteria.")
        return "\n".join(lines)
    for b in batches[:30]:
        lines.append(f"- **{b.get('item_name') or ('Item #'+str(b.get('item_id')))}** lot `{b.get('lot_code')}` (#{b.get('id')}): remaining **{b.get('quantity_remaining')}**/{b.get('quantity_received')}, received {b.get('received_at') or '-'}, expiry {b.get('expiry_date') or '-'} [{b.get('expiry_state') or b.get('status') or ''}], cost {_fmt_money(b.get('unit_cost',0))}, ref {b.get('reference') or '-'}")
    if len(batches) > 30:
        lines.append(f"- …and {len(batches)-30} more.")
    return "\n".join(lines)

def _fmt_trace_barcode(data: dict) -> str:
    if data.get("error"):
        return f"I couldn’t trace that code ({data.get('error')}). { 'Try a different barcode/SKU/lot code.' if 'not found' in str(data.get('error','')).lower() else '' }".strip()
    t = data.get("type")
    if t == "item":
        it = data.get("item") or {}
        lines = [f"Barcode/SKU resolved to inventory item (real data):"]
        lines.append(f"- **{it.get('item_name')}** (#{it.get('id')}), category {it.get('category') or '-'}, stock {it.get('stock_qty')} {it.get('unit') or ''}, sell {_fmt_money(it.get('selling_price',0))}, cost {_fmt_money(it.get('unit_cost',0))}")
        lines.append(f"- SKU: {it.get('sku') or '-'} | Barcode: {it.get('barcode') or '-'} | Batches tracked: {'yes' if it.get('track_batches') else 'no'} | Shelf life: {it.get('shelf_life_days') or '-'} days")
        return "\n".join(lines)
    if t == "batch":
        b = data.get("batch") or {}
        it = data.get("item") or {}
        lines = [f"Barcode/lot resolved to a traceable batch (real data):"]
        if it:
            lines.append(f"- Item: **{it.get('item_name')}** (#{it.get('id')}) SKU {it.get('sku') or '-'}")
        lines.append(f"- Lot: `{b.get('lot_code')}` (batch #{b.get('id')}), status {b.get('status') or '-'} [{b.get('expiry_state') or ''}]")
        lines.append(f"- Received: {b.get('received_at') or '-'} | Expiry: {b.get('expiry_date') or '-'} | Qty: remaining **{b.get('quantity_remaining')}** / received {b.get('quantity_received')}")
        lines.append(f"- Unit cost: {_fmt_money(b.get('unit_cost',0))} | Supplier: {b.get('supplier') or '-'} | Ref: {b.get('reference') or '-'}")
        if b.get('notes'): lines.append(f"- Notes: {_short(b.get('notes'), 140)}")
        moves = data.get("batch_movements") or []
        if moves:
            lines.append("Recent movements:")
            for m in moves[:8]:
                lines.append(f"  - {m.get('at','')}: {m.get('change_type','')} qty {m.get('quantity')} (ref {m.get('reference') or '-'})")
        return "\n".join(lines)
    return _format_data("Trace result", data)


def _fmt_app_update_status(data: dict) -> str:
    if data.get("error"):
        return f"I couldn’t read app update status ({data.get('error')})."
    local = data.get("local") or {}
    lines = ["App version and update status (local, grounded):"]
    lines.append(f"- App version: **{data.get('app_version') or local.get('app_version') or '—'}**")
    lines.append(f"- Installed commit: `{local.get('commit_short') or '—'}` {('— ' + local.get('commit_subject')) if local.get('commit_subject') else ''} {( '('+local.get('commit_date')+')' ) if local.get('commit_date') else ''}")
    lines.append(f"- Branch: {local.get('branch') or '—'} {'(local changes present)' if local.get('dirty') else ''}")
    if local.get('project_root'): lines.append(f"- Project root: {local.get('project_root')}")
    if local.get('data_dir'): lines.append(f"- Data directory (preserved on update/backup/restore): {local.get('data_dir')}")
    how = data.get('how_to_check') or "Use Settings → App updates to check for updates, view changelog, and apply safely (DB/exports/invoices preserved)."
    lines.append(f"- {how}")
    lines.append("- In-app (Web & TUI, git-based installs): check for updates, review changelog/details, and apply a safe fast-forward update.")
    lines.append("- Runtime data (DB, exports, invoices, configured data dir) is preserved; restart after apply. For safety, verify backups before/after updates.")
    return "\n".join(lines)


def _apply_proactive(intent: str, data: dict, answer: str) -> str:
    sugg = _proactive_suggestions(intent, data or {}, answer)
    if not sugg: return answer
    if "Want " in answer or "want " in answer.splitlines()[-1].lower():
        # avoid overdoing
        return answer
    return answer.rstrip() + "\n\n" + " ".join(sugg)

def _format_for_intent(intent: str, data: dict, fallback_title: str = "Result") -> str:
    try:
        if intent == "capabilities_help": return _fmt_capabilities(data)
        if intent == "property_stats": return _fmt_stats(data)
        if intent == "today_ops": return _fmt_today_ops(data)
        if intent == "business_partner_briefing": return _fmt_partner_briefing(data)
        if intent in ("report_pl","report_cash_flow","report_vat","report_payment_methods","report_occupancy","report_daily_ops"): return _fmt_report(data)
        if intent == "occupancy_summary": return _fmt_occupancy(data)
        if intent == "decision_support": return _fmt_decision_support(data)
        if intent == "compare_periods": return _fmt_compare(data)
        if intent == "outstanding_balances": return _fmt_outstanding(data)
        if intent == "inventory_alerts": return _fmt_inventory_alerts(data)
        if intent in ("expiring_batches","get_expiring_batches"): return _fmt_expiring_batches(data)
        if intent in ("inventory_batches","list_inventory_batches"): return _fmt_inventory_batches(data)
        if intent in ("trace_barcode","barcode_trace"): return _fmt_trace_barcode(data)
        if intent in ("app_update_status","get_app_update_status","app_version"): return _fmt_app_update_status(data)
        if intent == "top_selling_items": return _fmt_top_sellers(data)
        if intent == "expenses_summary": return _fmt_expenses(data)
        if intent == "sales_breakdown": return _fmt_sales_breakdown(data)
        if intent == "reservation_pipeline": return _fmt_pipeline(data)
        if intent == "housekeeping_summary": return _fmt_housekeeping(data)
        if intent == "room_availability": return _fmt_availability(data)
        if intent == "menu_info": return _fmt_menu(data)
        if intent == "rate_plans": return _fmt_rate_plans(data)
        if intent == "ledger_integrity": return _fmt_integrity(data)
        if intent in ("health_diagnostics", "doctor_report"): return _fmt_health_diagnostics(data)
        if intent == "recent_activity": return _fmt_recent_activity(data)
        if intent == "list_activities": return _fmt_activities_catalog(data)
        if intent == "activity_stats": return _fmt_activity_stats(data)
        if intent == "guest_activity_bookings": return _fmt_activity_bookings(data)
        if intent in ("reservation_summary","reservation_balance"): return _fmt_reservation(data)
        if intent == "guest_search": return _fmt_guest_search(data)
        if intent == "guest_profile": return _fmt_guest_profile(data)
        if intent == "guest_portfolio": return _fmt_guest_portfolio(data)
        if intent == "guest_last_visit": return _fmt_guest_last_visit(data)
        if intent == "guest_top_items_last_visit": return _fmt_guest_top_items(data)
        if intent == "guest_last_visit_spend": return _fmt_guest_last_visit_spend(data)
    except Exception:
        logger.warning("assistant._format_for_intent: formatter failed intent=%r; falling back to raw data", intent, exc_info=True)
    return _format_data(fallback_title, data)

# Deterministic local "talking" rephraser (no model, no external calls)
def _local_talking_rephrase(message: str, core_result: dict) -> str:
    """Produce a friendly, grounded natural reply from core result (pure local, deterministic)."""
    intent = (core_result or {}).get("intent") or ""
    data = core_result.get("data") or {}
    core_answer = (core_result.get("core_answer") or core_result.get("answer") or "").strip()
    if core_result.get("needs_clarification") or intent == "ambiguous_needs_clarification":
        # make clarification warm
        if core_answer:
            ans = core_answer
            if not ans.lower().startswith(("sure","of course","i can help","happy to")):
                ans = "Sure — " + ans[0].lower() + ans[1:] if len(ans)>1 else "Sure — " + ans
            return ans
    # Prefer well-formatted core answer (we now generate friendly ones); lightly frame if very terse
    if core_answer:
        ans = core_answer
        # avoid double-wrapping
        return ans
    # fallback: format from data
    return _format_for_intent(intent, data if isinstance(data, dict) else {}, fallback_title="Result")

