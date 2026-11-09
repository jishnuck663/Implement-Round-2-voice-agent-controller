# Grade report — public_02_incidents

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.294 | +20.5% | 0.202 | +10.5% | 0.508 | +7.6% | 44% | 63 (310) |
| nw_kyc | 0.374 | -2.7% | 0.241 | -2.5% | 0.597 | -0.4% | -8% | 23 (38) |
| nw_renewal | 0.414 | -3.8% | 0.258 | -2.0% | 0.538 | -1.7% | 6% | 44 (58) |
| ALL | 0.351 | +4.5% | 0.229 | +2.6% | 0.538 | +1.4% | 20% | 130 (406) |

## Changes applied by the controller

8 applied — neutral: 4, beneficial: 4

- ks_cancel v2 (controller_auto) dims=['welcome_message', 'voice'] engageΔ=+0.009 didΔ=+0.000 → **neutral**
- nw_renewal v2 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.006 didΔ=+0.000 → **neutral**
- nw_kyc v2 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.008 didΔ=+0.000 → **neutral**
- ks_cancel v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.193 → **beneficial**
- ks_cancel v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.161 → **beneficial**
- ks_cancel v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.034 → **beneficial**
- ks_cancel v7 (controller_approved) dims=['prompt_version'] engageΔ=+0.137 didΔ=+0.000 → **beneficial**
- ks_cancel v8 (controller_auto) dims=['welcome_message'] engageΔ=+0.010 didΔ=+0.000 → **neutral**

## Governance

```
{
  "approval_requests": 3,
  "decisions": {
    "approved": 3,
    "expired": 0
  },
  "rejection_reasons": {},
  "applied_via_approval": 3,
  "auto_applied": 5,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 1,
    "nw_kyc": 1,
    "ks_cancel": 1
  }
}
```

## Incidents

```
[
  {
    "type": "did_flagged",
    "workflow": "ks_cancel",
    "at": "D2 10:00",
    "flagged_did_share_by_day": {
      "2": 0.298,
      "3": 0.052,
      "4": 0.052,
      "5": 0.052,
      "6": 0.049,
      "7": 0.051,
      "8": 0.05,
      "9": 0.054
    }
  },
  {
    "type": "supervisor_edit",
    "workflow": "ks_cancel",
    "at": "D3 11:30",
    "mttr_minutes": 270,
    "fixed_by": "controller_approved"
  },
  {
    "type": "supervisor_edit",
    "workflow": "nw_renewal",
    "at": "D5 15:00"
  }
]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{}
```

Credits used: 30 · validation runs: 4 · LLM fix calls: 2