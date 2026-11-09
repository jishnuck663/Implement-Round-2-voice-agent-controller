# Grade report — public_01_dev

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.342 | +0.2% | 0.242 | +1.2% | 0.495 | -0.9% | 4% | 58 (52) |
| nw_kyc | 0.365 | -3.0% | 0.238 | -1.8% | 0.607 | -0.9% | 13% | 20 (21) |
| nw_renewal | 0.426 | +1.4% | 0.262 | +0.0% | 0.552 | +0.8% | 4% | 43 (39) |
| ALL | 0.375 | -0.1% | 0.248 | +0.2% | 0.537 | -0.3% | 6% | 121 (112) |

## Changes applied by the controller

3 applied — neutral: 2, beneficial: 1

- ks_cancel v2 (controller_auto) dims=['welcome_message', 'voice'] engageΔ=+0.006 didΔ=+0.000 → **neutral**
- nw_renewal v2 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.010 didΔ=+0.000 → **neutral**
- nw_kyc v2 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.030 didΔ=+0.000 → **beneficial**

## Governance

```
{
  "approval_requests": 2,
  "decisions": {
    "approved": 2,
    "expired": 0
  },
  "rejection_reasons": {},
  "applied_via_approval": 2,
  "auto_applied": 1,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 1,
    "nw_kyc": 1
  }
}
```

## Incidents

```
[]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0