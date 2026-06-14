# ─── step_e.py ────────────────────────────────────────────────────────────────
# Sits upstream of build_plan.
# Takes the raw credit score + session flags + intent,
# scores every module in the registry, resolves conflicts,
# and returns an ordered list of module IDs for build_plan to execute.
#
# No topic parsing. No tier labels. No translation layer.
# The module ID is the key — build_plan looks it up directly.
# ──────────────────────────────────────────────────────────────────────────────
 
 
# ─── SCORING REGISTRY ─────────────────────────────────────────────────────────
# These entries mirror the keys in build_plan's MODULE_REGISTRY exactly.
# Each entry defines how to score that module given score + session + intent.
#
#   score_range   → (min, max) raw FICO score where this module is relevant
#   session_keys  → session flags that add +10 confidence each when "yes"
#   session_kills → session flags that zero this module out entirely when "yes"
#   intent_match  → intents that add +10 confidence
#   base_score    → starting confidence before any signals are applied
#   conflicts     → module IDs that cannot coexist with this one
 
SCORING_REGISTRY = {
 
    "build_from_zero": {
        "score_range":   (300, 620),
        "session_keys":  [],                        # score alone is sufficient signal
        "session_kills": [],
        "intent_match":  ["improve", "start", "build"],
        "base_score":    60,
        "conflicts":     ["credit_score_basics_fair", "credit_score_basics_good", "credit_score_basics_excellent"],
    },
 
    "credit_score_basics_fair": {
        "score_range":   (580, 669),
        "session_keys":  [],
        "session_kills": [],
        "intent_match":  ["improve", "build"],
        "base_score":    55,
        "conflicts":     ["build_from_zero", "credit_score_basics_good", "credit_score_basics_excellent"],
    },
 
    "credit_score_basics_good": {
        "score_range":   (670, 739),
        "session_keys":  [],
        "session_kills": [],
        "intent_match":  ["improve", "optimize"],
        "base_score":    55,
        "conflicts":     ["build_from_zero", "credit_score_basics_fair", "credit_score_basics_excellent"],
    },
 
    "credit_score_basics_excellent": {
        "score_range":   (740, 850),
        "session_keys":  [],
        "session_kills": [],
        "intent_match":  ["optimize", "protect", "improve"],
        "base_score":    55,
        "conflicts":     ["build_from_zero", "credit_score_basics_fair", "credit_score_basics_good"],
    },
 
    "utilization": {
        "score_range":   (300, 780),               # relevant across almost every tier
        "session_keys":  ["high_utilization", "maxed_card"],
        "session_kills": [],
        "intent_match":  ["improve", "optimize", "lower"],
        "base_score":    45,
        "conflicts":     [],                        # stacks cleanly with most modules
    },
 
    "payment_history": {
        "score_range":   (300, 720),
        "session_keys":  ["missed_payment", "late_payment"],
        "session_kills": [],
        "intent_match":  ["improve", "fix", "recover"],
        "base_score":    45,
        "conflicts":     ["collections_first"],     # collections already covers payment recovery
    },
 
    "collections_first": {
        "score_range":   (300, 680),
        "session_keys":  ["paid_collections", "recent_collections", "medical_collections",
                          "multiple_collections", "collection_goal"],
        "session_kills": [],
        "intent_match":  ["fix", "recover", "remove", "improve"],
        "base_score":    30,                        # low base — session flags do the heavy lifting
        "conflicts":     ["payment_history"],
    },
 
    "credit_age": {
        "score_range":   (300, 850),               # age matters at every tier
        "session_keys":  ["closing_account", "authorized_user"],
        "session_kills": [],
        "intent_match":  ["protect", "improve", "optimize"],
        "base_score":    35,
        "conflicts":     [],
    },
}
 
 
# ─── THRESHOLDS ───────────────────────────────────────────────────────────────
# Minimum confidence a module needs to be selected.
# strict  → score + session data both present, raise the bar
# relaxed → score only, no session context yet, lower the bar
 
THRESHOLDS = {
    "strict":  65,
    "relaxed": 40,
}
 
 
# ─── MODE ─────────────────────────────────────────────────────────────────────
# strict  → user gave us a score AND answered session questions
# relaxed → we only have the score, no yes/no answers yet
 
def determine_mode(score: int, session: dict) -> str:
    has_session_data = any(v is not None for v in session.values())
    if score and has_session_data:
        return "strict"
    return "relaxed"
 
 
# ─── SCORE ONE MODULE ─────────────────────────────────────────────────────────
# Returns a 0–100 confidence value for a single module.
#
# Breakdown:
#   base_score              → starting relevance from registry
#   +25                     → raw score is inside the module's range
#   up to -30 penalty       → raw score is outside the range (fades with distance)
#   +10 per session_key     → each "yes" answer that confirms the problem
#   +10                     → intent matches
#   = 0 (hard kill)         → a session_kill flag is "yes"
 
def score_module(module: dict, score: int, session: dict, intent: str) -> float:
    # Hard kill check first
    for kill_key in module.get("session_kills", []):
        if session.get(kill_key) == "yes":
            return 0
 
    total = module["base_score"]
 
    # Score range gate
    low, high = module["score_range"]
    if low <= score <= high:
        total += 25
    else:
        # Penalize being off-range but don't zero out —
        # a 741 user with a missed payment still needs payment_history
        distance = min(abs(score - low), abs(score - high))
        penalty = min(distance // 10 * 5, 30)
        total -= penalty
 
    # Session signal boosts — each confirmed "yes" adds confidence
    for key in module.get("session_keys", []):
        if session.get(key) == "yes":
            total += 10
 
    # Intent boost
    if intent and intent in module.get("intent_match", []):
        total += 10
 
    return min(total, 100)
 
 
# ─── CONFLICT DETECTION ───────────────────────────────────────────────────────
# Returns (module_a, module_b) pairs where the two modules cannot coexist.
 
def detect_conflicts(priority: list) -> list:
    flags = []
    for i, mod_a in enumerate(priority):
        for mod_b in priority[i + 1:]:
            a_conflicts = SCORING_REGISTRY[mod_a].get("conflicts", [])
            b_conflicts = SCORING_REGISTRY[mod_b].get("conflicts", [])
            if mod_b in a_conflicts or mod_a in b_conflicts:
                flags.append((mod_a, mod_b))
    return flags
 
 
# ─── CONFLICT RESOLUTION ──────────────────────────────────────────────────────
# Drop the lower-scored module from each conflicting pair.
# In relaxed mode, only drop if the gap is meaningful (>10 pts) —
# too close to call means keep both.
 
def resolve_conflicts(priority: list, conflicts: list, weights: dict, mode: str) -> list:
    to_remove = set()
    for mod_a, mod_b in conflicts:
        if mod_a in to_remove or mod_b in to_remove:
            continue
        score_a = weights.get(mod_a, 0)
        score_b = weights.get(mod_b, 0)
        if mode == "relaxed" and abs(score_a - score_b) <= 10:
            continue
        loser = mod_b if score_a >= score_b else mod_a
        to_remove.add(loser)
    return [m for m in priority if m not in to_remove]
 
 
# ─── NORMALIZE ────────────────────────────────────────────────────────────────
# Converts raw weights to 0.0–1.0 values relative to the top scorer.
# Useful for showing the user how confident the routing decision was.
 
def normalize(weights: dict) -> dict:
    if not weights:
        return {}
    top = max(weights.values()) or 1
    return {k: round(v / top, 3) for k, v in weights.items()}
 
 
# ─── STEP E ───────────────────────────────────────────────────────────────────
# Main entry point — call this before build_plan.
#
# Args:
#   score   → raw credit score integer from score_shared (e.g. 612)
#   session → dict of yes/no answers collected during the conversation
#   intent  → what the user wants: "improve", "fix", "protect", "optimize"
#
# Returns:
#   {
#     "selected_modules": ordered list of module IDs for build_plan to run
#     "blocked_modules":  modules that scored below the threshold
#     "module_weights":   raw confidence score for every module
#     "confidence":       normalized 0.0–1.0 scores for selected modules only
#     "conflict_flags":   (mod_a, mod_b) pairs that were in conflict
#     "mode":             "strict" or "relaxed"
#   }
#
# build_plan usage:
#   routing = step_e(score, session, intent)
#   plan = build_plan(
#       topic   = routing["selected_modules"][0],
#       intent  = intent,
#       session = session,
#   )
 
def step_e(score: int, session: dict, intent: str = "improve") -> dict:
    session = session or {}
 
    # 1. Determine how much context we have
    mode = determine_mode(score, session)
 
    # 2. Score every module
    module_weights = {
        mod_id: score_module(module, score, session, intent)
        for mod_id, module in SCORING_REGISTRY.items()
    }
 
    # 3. Split into allowed vs blocked
    threshold = THRESHOLDS[mode]
    allowed  = []
    blocked  = []
    weights  = {}
 
    for mod_id, mod_score in module_weights.items():
        if mod_score >= threshold:
            allowed.append(mod_id)
            weights[mod_id] = mod_score
        else:
            blocked.append(mod_id)
 
    # 4. Sort by confidence descending
    priority = sorted(allowed, key=lambda m: weights[m], reverse=True)
 
    # 5. Detect and resolve conflicts
    conflicts = detect_conflicts(priority)
    priority  = resolve_conflicts(priority, conflicts, weights, mode)
 
    return {
        "selected_modules": priority,
        "blocked_modules":  blocked,
        "module_weights":   module_weights,
        "confidence":       normalize(weights),
        "conflict_flags":   conflicts,
        "mode":             mode,
    }
