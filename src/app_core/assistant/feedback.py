from __future__ import annotations

import difflib
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from .common import FEEDBACK_JSONL, INTENT_DATA_DIR, DEFAULT_SETFIT_MODEL_DIR, DEFAULT_SKLEARN_MODEL_PATH, _norm_text, _token_set
from .. import services

logger = logging.getLogger(__name__)

# ── Feedback & exemplar memory ───────────────────────────────────────────────
def _ensure_nlu_dirs():
    try:
        INTENT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("assistant._ensure_nlu_dirs: failed to create %s", INTENT_DATA_DIR, exc_info=True)

def record_feedback(message: str, predicted_intent: str = "", corrected_intent: str = "", rating: int = 0,
                    confidence: float | None = None, slots: dict | None = None, corrected_slots: dict | None = None,
                    tools_used: list | None = None, answer: str = "", user_note: str = "", source: str = "", status: str = "new"):
    _ensure_nlu_dirs()
    payload = {"created_at": datetime.now().strftime(getattr(services, "DATETIME_FMT", "%Y-%m-%d %H:%M:%S")),
               "message": message or "", "predicted_intent": predicted_intent or "", "corrected_intent": corrected_intent or "",
               "confidence": confidence, "slots": slots or {}, "corrected_slots": corrected_slots or {},
               "tools_used": tools_used or [], "answer_preview": (answer or "")[:500], "rating": int(rating or 0),
               "user_note": user_note or "", "status": status or "new", "source": source or ""}
    fid = None
    try:
        from ..db import connect
        cur = connect().execute(
            """INSERT INTO assistant_feedback (message,predicted_intent,corrected_intent,confidence,slots_json,corrected_slots_json,tools_used_json,answer_preview,rating,user_note,status,source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (payload["message"],payload["predicted_intent"],payload["corrected_intent"],payload["confidence"],
             json.dumps(payload["slots"],ensure_ascii=False,default=str), json.dumps(payload["corrected_slots"],ensure_ascii=False,default=str),
             json.dumps(payload["tools_used"],ensure_ascii=False,default=str), payload["answer_preview"], payload["rating"], payload["user_note"], payload["status"], payload["source"]))
        connect().commit(); fid = cur.lastrowid
    except Exception:
        fid = None
    try:
        with FEEDBACK_JSONL.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"id": fid, **payload}, ensure_ascii=False, default=str) + "\n")
    except OSError:
        logger.warning("assistant.record_feedback: failed to append feedback JSONL %s", FEEDBACK_JSONL, exc_info=True)
    global _EXEMPLAR_CACHE; _EXEMPLAR_CACHE = None
    return fid

def list_feedback(limit: int = 100, status: str | None = None):
    try:
        from ..db import connect
        q = "SELECT * FROM assistant_feedback"; args=[]
        if status: q += " WHERE status=?"; args.append(status)
        q += " ORDER BY id DESC LIMIT ?"; args.append(int(limit or 100))
        rows = connect().execute(q,args).fetchall(); out=[]
        for r in rows:
            d={k:r[k] for k in r.keys()}
            for j in ("slots_json","corrected_slots_json","tools_used_json"):
                try:
                    d[j.replace("_json","")] = json.loads(d.get(j) or ("{}" if "slots" in j else "[]"))
                except (json.JSONDecodeError, TypeError, ValueError):
                    logger.debug("assistant.list_feedback: failed to decode %s for feedback id=%s", j, d.get("id"), exc_info=True)
                    d[j.replace("_json","")] = {}
            out.append(d)
        return out
    except Exception:
        logger.warning("assistant.list_feedback: failed to read assistant feedback", exc_info=True)
        return []

def get_correction_exemplars(limit: int = 800):
    out=[]
    try:
        from ..db import connect
        rows = connect().execute(
            """SELECT message, corrected_intent, corrected_slots_json, predicted_intent, slots_json
               FROM assistant_feedback WHERE (corrected_intent != '' OR rating = 1) AND message != ''
               ORDER BY id DESC LIMIT ?""", (int(limit or 800),)).fetchall()
        for r in rows:
            intent = r["corrected_intent"] or r["predicted_intent"] or ""
            if not intent: continue
            try:
                slots = json.loads(r["corrected_slots_json"] or r["slots_json"] or "{}")
            except (json.JSONDecodeError, TypeError, ValueError):
                logger.debug("assistant.get_correction_exemplars: failed to decode slots for message=%r", (r["message"] or "")[:80], exc_info=True)
                slots = {}
            out.append({"text": r["message"], "label": intent, "slots": slots})
    except Exception:
        logger.debug("assistant.get_correction_exemplars: failed to load correction exemplars", exc_info=True)
    return out

_EXEMPLAR_CACHE = None
def _jaccard(a,b):
    if not a or not b: return 0.0
    inter=len(a&b); union=len(a|b); return inter/union if union else 0.0

def exemplar_match(text: str, threshold: float = 0.70):
    global _EXEMPLAR_CACHE
    if _EXEMPLAR_CACHE is None:
        _EXEMPLAR_CACHE = get_correction_exemplars(limit=1000)
    if not _EXEMPLAR_CACHE: return None
    qn=_norm_text(text); qset=_token_set(qn); best=None; best_score=0.0
    for ex in _EXEMPLAR_CACHE:
        tn=_norm_text(ex.get("text") or "")
        if not tn: continue
        if tn==qn:
            return {"intent":ex["label"],"confidence":0.99,"slots":ex.get("slots") or {},"source":"exemplar_exact"}
        score=max(_jaccard(qset,_token_set(tn)), difflib.SequenceMatcher(None, qn, tn).ratio()*0.95)
        if len(qn)>=8 and (qn in tn or tn in qn): score=max(score,0.9)
        if score>best_score: best_score=score; best=ex
    if best and best_score>=threshold:
        return {"intent":best["label"],"confidence":min(0.96,0.55+best_score*0.45),"slots":best.get("slots") or {},"source":"exemplar_similar","score":best_score}
    return None

# ── ML routers (optional, local/no external API) ─────────────────────────────
_SKLEARN_CACHE = None; _SETFIT_CACHE = None; _LOCAL_LLM_CACHE = None
def _load_sklearn_router():
    global _SKLEARN_CACHE
    if _SKLEARN_CACHE is not None: return _SKLEARN_CACHE
    try:
        import joblib  # type: ignore
        path = Path(os.environ.get("ASSISTANT_SKLEARN_MODEL", str(DEFAULT_SKLEARN_MODEL_PATH)))
        if not path.exists(): _SKLEARN_CACHE=False; return None
        _SKLEARN_CACHE=joblib.load(path); return _SKLEARN_CACHE
    except Exception: _SKLEARN_CACHE=False; return None

def _load_setfit_router():
    global _SETFIT_CACHE
    if _SETFIT_CACHE is not None: return _SETFIT_CACHE
    try:
        from setfit import SetFitModel  # type: ignore
        path = Path(os.environ.get("ASSISTANT_SETFIT_MODEL_DIR", str(DEFAULT_SETFIT_MODEL_DIR)))
        if not path.exists(): _SETFIT_CACHE=False; return None
        _SETFIT_CACHE=SetFitModel.from_pretrained(str(path)); return _SETFIT_CACHE
    except Exception: _SETFIT_CACHE=False; return None

def _predict_intent_ml(text: str):
    mode=(os.environ.get("ASSISTANT_NLU") or "auto").strip().lower()
    if mode in ("auto","setfit"):
        model=_load_setfit_router()
        if model is not None:
            try:
                labels=model.predict([text]); label=labels[0] if isinstance(labels,(list,tuple)) else labels
                conf=0.7
                try:
                    probs=model.predict_proba([text]); p=probs[0] if hasattr(probs,"__len__") else probs
                    arr=list(p.tolist() if hasattr(p,"tolist") else p); conf=float(max(arr)) if arr else conf
                except Exception:
                    logger.debug("assistant._predict_intent_ml: setfit predict_proba unavailable; using default confidence", exc_info=True)
                return {"intent":str(label),"confidence":float(conf),"router":"setfit"}
            except Exception:
                logger.debug("assistant._predict_intent_ml: setfit prediction failed", exc_info=True)
        if mode=="setfit": return None
    if mode in ("auto","sklearn"):
        obj=_load_sklearn_router()
        if obj is not None:
            try:
                vec=obj.get("vectorizer") or obj.get("vec"); clf=obj.get("classifier") or obj.get("clf") or obj.get("model")
                if vec is not None and clf is not None:
                    X=vec.transform([text]); pred=clf.predict(X)[0]; conf=0.65
                    try:
                        if hasattr(clf,"predict_proba"): conf=float(max(clf.predict_proba(X)[0]))
                        elif hasattr(clf,"decision_function"):
                            import math
                            df=clf.decision_function(X); vals=df[0] if getattr(df,"ndim",1)>1 else df
                            exps=[math.exp(float(v)) for v in (vals.tolist() if hasattr(vals,"tolist") else list(vals))]; s=sum(exps) or 1.0
                            conf=float(max(exps)/s)
                    except Exception:
                        logger.debug("assistant._predict_intent_ml: sklearn confidence estimation failed", exc_info=True)
                    return {"intent":str(pred),"confidence":float(conf),"router":"sklearn"}
            except Exception:
                logger.debug("assistant._predict_intent_ml: sklearn prediction failed", exc_info=True)
    return None

