"""Synthetic, read-only research accounting and serial next-request decisions.

No function reserves, settles, persists, sends HTTP, or authorizes a send. Ledger
remains the authority. Only its default synthetic price/bound policy is supported.

project_costs inputs (all JSON values):
* request_rows: [{ledger_id, row, envelope, attribution}]. ``row`` is an unchanged
  a0_ledger row; ``envelope`` is exactly what Ledger.reserve hashed, including the
  supplier-attempt request_id, which must equal row.id. Attribution contains
  role_id/task_id/run_id/branch_id/parent_checkpoint_id/stage, and optionally
  snapshot_id. One immutable billing owner; other roles/paths may reference it.
* lineage: {ledger_slices, branches, checkpoints}. A slice is
  {state, sha256, poisoned}, from the owning Ledger, including empty/closed ones.
  A branch is {run_id, parent_checkpoint_id, request_ids}; a checkpoint is
  {branch_id, request_ids}, an explicit prefix of that branch's request_ids.
  Checkpoint ancestry follows the branch's parent checkpoint, never a segment
  name. Branch lists may reference shared requests, but cannot omit their own.
* budget_policy: {synthetic: True, ledgers, attributions, limits}. ``attributions``
  freezes request_id -> digest(attribution) for every recorded attempt. ``ledgers`` freezes each
  slice's {pilot, manifest, prices_sha256, limits_sha256}. ``limits`` requires
  research and stages (formation/probe/question/verification); optional roles,
  runs, branches, paths map scope IDs to limits. Every limit has positive integer
  tokens/micro_cny/attempts. Values are fixtures, not formal B/Q or live quotes.

admit_request additionally needs ledger_id, request, allowance, attribution,
ledger_cut, current_ledger_slices, and mode='serial'. The caller freezes policy
identities and attribution fingerprints; none of these hashes is a signature.
Reissuing policy and changing its fingerprints together is outside this validator.
Current slices must be
recaptured by the owner; a hash does not establish freshness by itself. Rebuild
projection and decide again after any change. Even an allowed result always has
send_authorized=False: atomic reserve and real HTTP integration remain separate.
"""
from __future__ import annotations

from copy import deepcopy

try:
    from .a0_ledger import canonical, cost, digest, price_table
except ImportError:  # Direct experiments/ script imports.
    from a0_ledger import canonical, cost, digest, price_table


STAGES = ("formation", "probe", "question", "verification")
METRICS = ("tokens", "micro_cny", "attempts")
ATTRIBUTION = {"role_id", "task_id", "run_id", "branch_id", "parent_checkpoint_id", "stage"}


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _name(value):
    return isinstance(value, str) and bool(value)


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _attribution(value):
    _require(isinstance(value, dict) and ATTRIBUTION <= value.keys()
             and not value.keys() - ATTRIBUTION - {"snapshot_id"}, "invalid attribution")
    _require(all(_name(value[key]) for key in ATTRIBUTION - {"parent_checkpoint_id"})
             and (value["parent_checkpoint_id"] is None or _name(value["parent_checkpoint_id"]))
             and value["stage"] in STAGES, "invalid attribution identity/stage")
    _require("snapshot_id" not in value or _name(value["snapshot_id"]), "invalid snapshot_id")


def _limits(policy):
    _require(set(policy) == {"synthetic", "ledgers", "attributions", "limits"} and policy["synthetic"] is True,
             "only explicit synthetic budget policies are supported")
    limits = policy["limits"]
    _require(isinstance(limits, dict) and {"research", "stages"} <= limits.keys()
             and not limits.keys() - {"research", "stages", "roles", "runs", "branches", "paths"},
             "missing/unknown budget scope")
    _require(set(limits["stages"]) == set(STAGES), "all research stages need explicit limits")
    groups = [("research", limits["research"])]
    for kind, entries in limits.items():
        if kind != "research":
            _require(isinstance(entries, dict) and all(_name(key) for key in entries), "invalid scope IDs")
            groups.extend((kind + "/" + key, value) for key, value in entries.items())
    for _, values in groups:
        _require(isinstance(values, dict) and set(values) == set(METRICS)
                 and all(_integer(number, 1) for number in values.values()), "invalid budget limits")
    return dict(groups)


def _zero():
    return dict.fromkeys(METRICS, 0)


def _sum(requests, ids, field):
    total = _zero()
    for request_id in sorted(set(ids)):
        for metric in METRICS:
            total[metric] += requests[request_id][field][metric]
    return total


def _slice_rows(slices, identities):
    _require(isinstance(slices, dict) and bool(slices) and set(slices) == set(identities),
             "missing/unknown ledger slice")
    rows, blockers = {}, []
    for ledger_id, slice_ in slices.items():
        _require(_name(ledger_id) and set(slice_) == {"state", "sha256", "poisoned"}
                 and type(slice_["poisoned"]) is bool, "invalid ledger slice")
        state = slice_["state"]
        _require(slice_["sha256"] == digest(state), "ledger checksum drift")
        _require(set(state) == {"schema", "pilot", "manifest", "limits", "prices", "wakes", "closed", "requests"}
                 and type(state["schema"]) is int and state["schema"] == 1
                 and _name(state["pilot"]) and _name(state["manifest"])
                 and isinstance(state["closed"], str) and isinstance(state["requests"], list),
                 "invalid ledger state")
        identity = {"pilot": state["pilot"], "manifest": state["manifest"],
                    "prices_sha256": digest(state["prices"]), "limits_sha256": digest(state["limits"])}
        _require(identity == identities[ledger_id], "ledger identity/manifest/price/limit drift")
        price_table(state["prices"])  # Missing prices never become zero.
        limits = state["limits"]
        _require(set(limits) == {"wakes", "attempts", "output", "allowances", "context", "micro_cny"}
                 and all(_integer(value, 1) for value in limits.values())
                 and _integer(state["wakes"]) and state["wakes"] <= limits["wakes"], "invalid Ledger limits")
        if slice_["poisoned"]:
            blockers.append(ledger_id + ":persistence-unknown")
        if state["closed"]:
            blockers.append(ledger_id + ":closed:" + state["closed"])
        inflight = 0
        previous_settled = None
        for index, row in enumerate(state["requests"], 1):
            request_id = row["id"]
            _require(request_id == f"{state['pilot']}/{index:04d}" and request_id not in rows,
                     "duplicate/out-of-order Ledger request ID")
            _require(row["status"] in {"reserved", "settled"}
                     and _integer(row["input_bound"], 1) and row["input_bound"] <= limits["context"]
                     and _integer(row["allowance"], 1) and row["allowance"] <= limits["output"]
                     and _integer(row["reserved"])
                     and row["reserved"] == cost(state["prices"], row["input_bound"], 0, row["allowance"], reserve=True),
                     "invalid Ledger reservation")
            _require(isinstance(row["requested_at"], (int, float))
                     and (previous_settled is None or row["requested_at"] >= previous_settled),
                     "out-of-order request/settlement")
            if row["status"] == "reserved":
                inflight += 1
                _require(index == len(state["requests"]) and not {"usage", "charged", "settled_at"} & row.keys(),
                         "in-flight row must be last and unsettled")
                blockers.append(ledger_id + ":inflight")
            else:
                usage = row["usage"]
                _require(usage is None or isinstance(usage, list) and len(usage) == 3, "invalid usage shape")
                charged = row["reserved"] if usage is None else cost(state["prices"], *usage)
                _require(_integer(row["charged"]) and row["charged"] == charged,
                         "settled charge/usage conflict")
                _require(isinstance(row["settled_at"], (int, float))
                         and row["settled_at"] >= row["requested_at"], "out-of-order settlement")
                previous_settled = row["settled_at"]
                if usage is None:
                    blockers.append(ledger_id + ":missing-usage")
                elif usage[0] > row["input_bound"] or usage[2] > row["allowance"] or charged > row["reserved"]:
                    blockers.append(ledger_id + ":usage-overreport")
                _require(_name(row["usage_source"]) and _name(row["outcome"]), "missing settlement evidence")
                if row["outcome"] != "stop":
                    blockers.append(ledger_id + ":outcome:" + row["outcome"])
            rows[request_id] = (ledger_id, row)
        _require(inflight <= 1 and len(state["requests"]) <= limits["attempts"]
                 and sum(row["allowance"] for row in state["requests"]) <= limits["allowances"],
                 "corrupt serial Ledger limits")
    return rows, blockers


def _paths(lineage, requests):
    branches, checkpoints = lineage["branches"], lineage["checkpoints"]
    _require(isinstance(branches, dict) and isinstance(checkpoints, dict), "invalid lineage")
    for branch_id, branch in branches.items():
        _require(_name(branch_id) and set(branch) == {"run_id", "parent_checkpoint_id", "request_ids"}
                 and _name(branch["run_id"]), "invalid branch")
        ids = branch["request_ids"]
        _require(isinstance(ids, list) and len(ids) == len(set(ids)) and set(ids) <= requests.keys(),
                 "unknown/duplicate branch request")
        parent = branch["parent_checkpoint_id"]
        _require(parent is None or parent in checkpoints, "unknown ancestor checkpoint")
    for checkpoint_id, checkpoint in checkpoints.items():
        _require(_name(checkpoint_id) and set(checkpoint) == {"branch_id", "request_ids"}
                 and checkpoint["branch_id"] in branches, "unknown checkpoint branch")
        ids = checkpoint["request_ids"]
        _require(isinstance(ids, list) and ids == branches[checkpoint["branch_id"]]["request_ids"][:len(ids)],
                 "checkpoint requests must be an explicit branch prefix")
        _require(all(requests[key]["status"] == "settled" for key in ids), "unsettled checkpoint")
    for request_id, request in requests.items():
        attr = request["attribution"]
        _require(attr["branch_id"] in branches, "unknown owning branch")
        owner = branches[attr["branch_id"]]
        _require(request_id in owner["request_ids"] and attr["run_id"] == owner["run_id"]
                 and attr["parent_checkpoint_id"] == owner["parent_checkpoint_id"],
                 "request owner/lineage drift or omitted request")
    visiting, ancestors = set(), {}

    def checkpoint_requests(checkpoint_id):
        if checkpoint_id is None:
            return set()
        _require(checkpoint_id not in visiting, "lineage cycle")
        if checkpoint_id not in ancestors:
            visiting.add(checkpoint_id)
            checkpoint = checkpoints[checkpoint_id]
            parent = branches[checkpoint["branch_id"]]["parent_checkpoint_id"]
            ancestors[checkpoint_id] = checkpoint_requests(parent) | set(checkpoint["request_ids"])
            visiting.remove(checkpoint_id)
        return ancestors[checkpoint_id]

    for key in checkpoints:
        checkpoint_requests(key)
    return {key: {"ancestor_request_ids": sorted(checkpoint_requests(branch["parent_checkpoint_id"])),
                  "request_ids": sorted(checkpoint_requests(branch["parent_checkpoint_id"]) | set(branch["request_ids"]))}
            for key, branch in branches.items()}


def _project_costs(request_rows, lineage, budget_policy):
    """Validate complete synthetic Ledger slices and return recomputable costs.

    Malformed/conflicting input raises ValueError; intact closed/unknown/in-flight
    state is reported with its full conservative cost and global blockers.
    """
    source = deepcopy({"request_rows": request_rows, "lineage": lineage, "budget_policy": budget_policy})
    request_rows, lineage, budget_policy = (source[key] for key in ("request_rows", "lineage", "budget_policy"))
    _require(set(lineage) == {"ledger_slices", "branches", "checkpoints"}, "missing lineage/slices")
    configured_limits = _limits(budget_policy)
    ledger_rows, blockers = _slice_rows(lineage["ledger_slices"], budget_policy["ledgers"])
    _require(isinstance(budget_policy["attributions"], dict)
             and set(budget_policy["attributions"]) == set(ledger_rows), "missing/frozen attribution coverage")
    _require(isinstance(request_rows, list), "request_rows must be a list")
    requests, seen = {}, {}
    for record in request_rows:
        _require(set(record) == {"ledger_id", "row", "envelope", "attribution"}, "invalid request record")
        row, envelope, attr = record["row"], record["envelope"], record["attribution"]
        _attribution(attr)
        request_id = envelope["request_id"]
        _require(request_id == row["id"] and request_id in ledger_rows, "unknown/conflicting request ID")
        _require(digest(attr) == budget_policy["attributions"][request_id], "frozen attribution drift")
        _require(ledger_rows[request_id] == (record["ledger_id"], row)
                 and digest(envelope) == row["request_sha256"]
                 and row["input_bound"] == len(canonical(envelope)) + 1024, "request envelope/usage/bound conflict")
        if request_id in seen:
            _require(seen[request_id] == record, "conflicting duplicate request/attribution")
            continue  # Repeated references are not repeated settlements.
        seen[request_id] = record
        usage = row.get("usage")
        known = row["status"] == "settled" and usage is not None
        tokens = usage[0] + usage[2] if known else row["input_bound"] + row["allowance"]
        charged = row["charged"] if row["status"] == "settled" else row["reserved"]
        accounted = {"tokens": tokens, "micro_cny": charged, "attempts": 1}
        requests[request_id] = {"ledger_id": record["ledger_id"], "status": row["status"],
                                "attribution": attr, "usage": usage, "usage_known": known,
                                "ordinary_input": usage[0] - usage[1] if known else None,
                                "cached_input": usage[1] if known else None,
                                "output": usage[2] if known else None,
                                "accounted": accounted,
                                "S": accounted if row["status"] == "settled" else _zero(),
                                "I": accounted if row["status"] == "reserved" else _zero(),
                                "actual": accounted if known else _zero(),
                                "unknown_reserve": _zero() if known else accounted}
    _require(set(requests) == set(ledger_rows), "missing request attribution/ledger coverage")
    paths = _paths(lineage, requests)
    scope_ids = {"research": set(requests)}
    for kind, field in (("stages", "stage"), ("roles", "role_id"), ("runs", "run_id"), ("branches", "branch_id")):
        for request_id, request in requests.items():
            scope_ids.setdefault(kind + "/" + request["attribution"][field], set()).add(request_id)
    for branch_id, path in paths.items():
        scope_ids["paths/" + branch_id] = set(path["request_ids"])
        path["cost"] = _sum(requests, path["request_ids"], "accounted")
        path["ancestor_cost"] = _sum(requests, path["ancestor_request_ids"], "accounted")
    for key in configured_limits:
        scope_ids.setdefault(key, set())
    scopes = {key: {field: _sum(requests, ids, field) for field in ("S", "I", "actual", "accounted", "unknown_reserve")}
              for key, ids in scope_ids.items()}
    unknown = sorted(key for key, value in requests.items() if not value["usage_known"])
    result = {"schema": 1, "ledger_cut": digest(source), "source": source,
              "requests": requests, "scopes": scopes, "paths": paths,
              "unknown_usage_request_ids": unknown, "blockers": sorted(set(blockers)),
              "limits": configured_limits, "send_authorized": False}
    return result


def project_costs(request_rows, lineage, budget_policy):
    """Return a validated synthetic projection; reject inconsistent input.

    S includes conservative full-reserve settlements; I holds outstanding
    reservations. ``actual`` contains only known usage, and ``unknown_reserve``
    exposes the retained amount separately. Neither is another writable ledger.
    """
    try:
        return _project_costs(request_rows, lineage, budget_policy)
    except (KeyError, TypeError, IndexError, ArithmeticError) as error:
        raise ValueError("invalid accounting input: " + str(error)) from error


def admit_request(projection, request_envelope):
    """Decide S + I + R on supplied, freshly captured slices; never send authority.

    All scopes use deduplicated actual attempts. Paths additionally include the
    explicit checkpoint ancestor set. Existing unknown/poison/inflight state
    blocks *all* roles and branches. Unsupported concurrency is refused.
    """
    reasons, formulas = [], {}
    result = {"allowed": False, "send_authorized": False, "mode": "serial",
              "ledger_cut": projection.get("ledger_cut"), "reasons": reasons, "formulas": formulas}
    try:
        rebuilt = project_costs(**projection["source"])
        _require(rebuilt == projection, "projection drift")
        value = request_envelope
        _require(set(value) == {"ledger_id", "request", "allowance", "attribution", "ledger_cut",
                                "current_ledger_slices", "mode"}, "missing/unknown admission envelope fields")
        _require(value["mode"] == "serial", "concurrent admission unsupported")
        _require(value["ledger_cut"] == projection["ledger_cut"]
                 and value["current_ledger_slices"] == projection["source"]["lineage"]["ledger_slices"],
                 "stale ledger cut; rebuild projection")
        attr = value["attribution"]
        _attribution(attr)
        lineage = projection["source"]["lineage"]
        _require(attr["branch_id"] in lineage["branches"], "unknown admission branch")
        branch = lineage["branches"][attr["branch_id"]]
        _require(attr["run_id"] == branch["run_id"] and attr["parent_checkpoint_id"] == branch["parent_checkpoint_id"],
                 "admission lineage drift")
        _require(value["ledger_id"] in lineage["ledger_slices"], "unknown admission ledger")
        state = lineage["ledger_slices"][value["ledger_id"]]["state"]
        request, allowance = value["request"], value["allowance"]
        _require(request["request_id"] == f"{state['pilot']}/{len(state['requests']) + 1:04d}"
                 and request["request_id"] not in projection["requests"], "new attempt requires next unique request_id")
        bound = len(canonical(request)) + 1024
        limits = state["limits"]
        _require(_integer(allowance, 1) and allowance <= limits["output"] and bound <= limits["context"],
                 "Ledger input/output limit")
        reserve = {"tokens": bound + allowance, "micro_cny": cost(state["prices"], bound, 0, allowance, reserve=True),
                   "attempts": 1}
        result["reservation"] = reserve
        result["request_sha256"] = digest(request)
        result["attribution_sha256"] = digest(attr)
        reasons.extend(projection["blockers"])
        if len(state["requests"]) + 1 > limits["attempts"]:
            reasons.append("Ledger attempt limit")
        if sum(row["allowance"] for row in state["requests"]) + allowance > limits["allowances"]:
            reasons.append("Ledger cumulative allowance limit")
        if sum(row.get("charged", row["reserved"]) for row in state["requests"]) + reserve["micro_cny"] > limits["micro_cny"]:
            reasons.append("Ledger cumulative CNY limit")
        affected = {"research", "stages/" + attr["stage"], "roles/" + attr["role_id"],
                    "runs/" + attr["run_id"], "branches/" + attr["branch_id"], "paths/" + attr["branch_id"]}
        for scope, ceiling in projection["limits"].items():
            usage = projection["scopes"][scope]
            formulas[scope] = {}
            for metric in METRICS:
                S, I = usage["S"][metric], usage["I"][metric]
                R = reserve[metric] if scope in affected else 0
                formulas[scope][metric] = {"S": S, "I": I, "R": R, "total": S + I + R,
                                          "limit": ceiling[metric], "ok": S + I + R <= ceiling[metric]}
                if S + I + R > ceiling[metric]:
                    reasons.append(scope + ":" + metric + " limit")
        result["allowed"] = not reasons
    except (ValueError, KeyError, TypeError, IndexError) as error:
        reasons.append(str(error))
    return result
