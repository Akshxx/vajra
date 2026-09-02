# Razorpay Merchant Ecosystem — Complete Guide for VAJRA

> **Understanding your target users: Merchants on Razorpay**

---

## **1. Who Are Razorpay Merchants?**

| Segment | Examples | Typical Volume | Pain Points |
|---------|----------|----------------|-------------|
| **E-commerce** | D2C brands, Shopify/WooCommerce stores | 1K-100K orders/mo | Chargebacks, COD returns, fraud |
| **SaaS/B2B** | EdTech, subscription platforms | Recurring billing | Failed mandates, involuntary churn |
| **Marketplaces** | Food delivery, gig platforms, vendor platforms | High volume, split payouts | Vendor fraud, settlement reconciliation |
| **Offline/Retail** | POS, QR codes, Payment Links | Mixed online/offline | Refund fraud, chargebacks |
| **Enterprise** | Travel, BFSI, large retail | High ticket, international | Cross-border fraud, compliance |

**Key Insight:** Merchants **don't build fraud systems** — they integrate Razorpay and expect it to "just work." When chargebacks/fraud hit, they lose revenue AND time.

---

## **2. Merchant Onboarding & Account Structure**

```
┌─────────────────────────────────────────────────────────────┐
│                    RAZORPAY MERCHANT ACCOUNT                │
├─────────────────────────────────────────────────────────────┤
│  • KYC Verification (mandatory before settlements)         │
│  • Test Mode (sandbox) → Live Mode switch                  │
│  • API Keys: Key ID (public) + Key Secret (private)        │
│  • Webhook endpoints for real-time events                  │
│  • Dashboard: Payments, Orders, Customers, Settlements     │
│  • Sub-merchants (for marketplaces via Route)              │
└─────────────────────────────────────────────────────────────┘
```

**Test Mode:** Same API URL (`api.razorpay.com/v1`), different keys. No real money moves.

---

## **3. Payment Flow (What Merchants Experience)**

### **Standard Checkout Flow**
```
Customer → Merchant Site/App → Razorpay Checkout → Bank/Network → 
  → Razorpay → Webhook to Merchant → Settlement (T+2)
```

### **Key Entities Merchants Deal With**
| Entity | Description | API Resource |
|--------|-------------|--------------|
| **Payment** | Single transaction | `/payments/{id}` |
| **Order** | Merchant's order record (pre-payment) | `/orders/{id}` |
| **Customer** | Saved customer profile | `/customers/{id}` |
| **Refund** | Full/partial refund | `/payments/{id}/refund` |
| **Settlement** | Batch payout to bank | `/settlements/{id}` |
| **Dispute/Chargeback** | Customer/bank challenges payment | `/disputes/{id}` |

---

## **4. Chargeback/Dispute Process (Your Core Problem Space)**

### **Dispute Phases (from Razorpay Docs)**
```
1. RETRIEVAL (Soft chargeback) → Bank asks for info
2. CHARGEBACK → Customer claims fraud/not received
3. PRE-ARBITRATION → Merchant won, customer challenges again
4. ARBITRATION → Card networks get involved (costly!)
```

### **Dispute States in Razorpay**
| State | Meaning | Merchant Action |
|-------|---------|-----------------|
| **Open** | Dispute created | Decide: Accept or Contest |
| **Under Review** | Bank reviewing evidence | Wait |
| **Won** | Bank accepted evidence | Money stays |
| **Lost** | Bank rejected evidence | Money deducted |
| **Closed** | Fraud resolved (refund given) | Done |

### **Evidence Submission (Critical for VAJRA)**
Merchants **must submit evidence** within deadlines (typically 7-14 days). Razorpay provides reason-code-specific evidence requirements:

| Reason Code Category | Example Codes | Evidence Required |
|---------------------|---------------|-------------------|
| **Fraud (Card-Not-Present)** | Visa 10.4, MC 4837 | AVS match, CVV, 3DS, IP/device fingerprint, shipping to billing address |
| **Not Received** | Visa 13.1, MC 4853 | Delivery confirmation, tracking, signature, digital delivery logs |
| **Not as Described** | Visa 13.3, MC 4853 | Product images, quality control, return policy, no return received |
| **Credit Not Processed** | Visa 13.6, MC 4860 | Refund proof, bank statement, return confirmation |

### **Merchant Pain Points in Disputes**
1. **Manual evidence gathering** — PDFs, screenshots, logs from different systems
2. **Tight deadlines** — 7-14 days, miss it = auto-loss
3. **No guidance** — "Here's the reason code, figure out what to submit"
3. **Low win rates** — Industry average ~35%
4. **No learning** — Same mistakes repeat, no pattern recognition

---

## **5. Settlement & Reconciliation (Track 4 Territory)**

### **Standard Settlement: T+2 Working Days**
- Domestic: T+2 (capture date + 2 working days)
- International: T+7 (varies by country)
- **Instant Settlements:** On-demand, minutes (feature request)

### **Partial Settlements**
When live balance < scheduled settlement (due to refunds), Razorpay settles what's available, rest next cycle.

### **Settlement Failure Reasons**
- Bank account inactive/frozen
- Incorrect account details
- Bank rejection

### **Reconciliation Needs (Merchant Side)**
- Match Razorpay settlements → Bank statements
- Handle failed settlements, partial settlements
- Refund adjustments across cycles
- Fee verification (MDR, GST, TDS)

---

## **6. Fraud Prevention (What Razorpay Provides Today)**

### **Built-in (Automatic)**
- **Risk Engine:** Real-time scoring (rules + ML)
- **3D Secure:** Mandatory for cards, optional for UPI
- **Velocity Checks:** Basic transaction frequency limits
- **AVS/CVV:** Standard card verification

### **Merchant-Configurable**
- **Block/Allow Lists:** By card BIN, country, email domain
- **Custom Rules:** Via dashboard (limited)
- **Webhooks:** Real-time payment.dispute_created, payment.failed, etc.

### **Gaps Merchants Feel**
- No **explanations** for why a transaction was flagged
- No **causal attribution** ("this device caused the risk")
- No **automated evidence compilation** for chargebacks
- No **revenue recovery** for failed payments/abandoned carts
- **Settlement reconciliation** is manual spreadsheet work

---

## **7. Webhook Events (Real-Time Integration)**

### **Key Events VAJRA Should Consume**
```json
// Payment completed
{
  "event": "payment.captured",
  "payload": {
    "payment": {
      "id": "pay_xxx",
      "amount": 5000,
      "currency": "INR",
      "method": "card",
      "card": {"network": "Visa", "last4": "1234"},
      "customer": {"id": "cust_xxx", "email": "..."},
      "order_id": "order_xxx"
    }
  }
}

// Chargeback initiated
{
  "event": "dispute.created",
  "payload": {
    "dispute": {
      "id": "disp_xxx",
      "payment_id": "pay_xxx",
      "amount": 5000,
      "reason_code": "fraudulent",
      "phase": "chargeback",
      "status": "open",
      "created_at": 1699900000
    }
  }
}

// Settlement
{
  "event": "settlement.processed",
  "payload": {
    "settlement": {
      "id": "stl_xxx",
      "amount": 450000,
      "fees": 5000,
      "utr": "AXISxxxxxx",
      "status": "processed"
    }
  }
}
```

---

## **8. How VAJRA Maps to Merchant Needs**

| Merchant Pain Point | VAJRA Solution | API Integration Point |
|---------------------|-------------------|----------------------|
| **Chargeback loss** | Tribunal Engine → auto-evidence → 60% win rate | `POST /chargebacks/defend` |
| **Fraud not caught** | Causal Fraud Graph → ring detection + counterfactuals | `POST /fraud/explain` |
| **False positives** | Cost-aware decisions → ₹1,500 FP cost vs ₹8,000 baseline | Built into tribunal |
| **No audit trail** | Merkle-ledger → immutable proof for regulators | `GET /audit/trail/{id}` |
| **Manual reconciliation** | ReconX (Track 4) → program-synthesized matching | Future |
| **Failed payment recovery** | RecoverX (Track 3) → root cause → retry sequence | Future |

---

## **9. VAJRA Demo Narrative for Merchants**

### **What to Say in Demo**
> "You're a fashion brand on Razorpay. Last month you got 47 chargebacks. You manually gathered PDFs for each, missed 3 deadlines, won only 12. 
> 
> **With VAJRA:** 
> 1. Webhook fires `dispute.created` → VAJRA auto-creates case
> 2. Tribunal agents debate: Prosecutor finds your shipping proof + AVS match + 12 clean orders. Defense claims 'not received.' Judge rules: SUBMIT_EVIDENCE (87% confidence).
> 3. Policy auto-generates: collects tracking PDF, AVS log, customer history → submits to Visa arbitration.
> 4. Audit trail logs every step with Merkle proof. You win 28/47 instead of 12/47.
> 5. Fraud Vajra catches the card-testing ring *before* it hits you — 1 device, 15 cards, blocked at checkout."

---

## **10. Test Mode for Hackathon**

### **Your Current Setup Uses Test Mode**
- Database: Local PostgreSQL
- LLM: Mock client (returns realistic tribunal debates)
- Fraud Graph: In-memory + persisted
- No real Razorpay API calls needed

### **To Simulate Real Webhooks for Demo**
```python
# In your demo script, simulate Razorpay webhook payloads
# Your /transactions/ingest endpoint accepts the same structure
# as Razorpay's payment.captured webhook payload
```

---

## **11. Key Metrics to Quote (From Razorpay Data)**

| Metric | Industry Baseline | VAJRA Target |
|--------|-------------------|-----------------|
| Chargeback win rate | 35% | **≥60%** |
| False positive cost | ₹8,000/10k txns | **≤₹1,500/10k** |
| Fraud precision @80% recall | 65% | **≥85%** |
| Chargeback response time | Manual (hours) | **<5 min automated** |
| Settlement reconciliation | Manual (days) | **Automated (minutes)** |

---

## **12. Regulatory/Compliance Context (India)**

- **RBI Guidelines:** Payment aggregators must have dispute resolution
- **Card Network Rules:** Visa/MC/Rupay mandate evidence submission timelines
- **Data Localization:** Payment data must stay in India
- **Audit Requirements:** 7-year retention for financial records
- **VAJRA Advantage:** Immutable audit ledger + Merkle proofs = regulatory ready

---

## **Summary: Your Target User**

> **A Razorpay merchant** — typically a D2C brand, SaaS company, or marketplace — who:
> - Processes ₹10L-₹100Cr/year
> - Loses 0.5-1% to chargebacks + fraud
> - Has NO dedicated fraud team
> - Manually handles disputes today
> - Needs: **Automated, explainable, auditable defense** that integrates with their existing Razorpay webhooks
> - Buys: **Revenue protection** (not "fraud detection")

**VAJRA sells:** "We turn your chargeback losses into recovered revenue, automatically, with proof."