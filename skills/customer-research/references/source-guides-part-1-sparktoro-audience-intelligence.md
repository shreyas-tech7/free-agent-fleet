## SparkToro (Audience Intelligence)

SparkToro is a behavioral audience research tool. Instead of mining individual posts and comments, it aggregates clickstream, search, and social data to show what your audience does at scale — what they read, watch, listen to, follow, and search for.

### When to Use SparkToro vs. Manual Research

- **SparkToro first** when you need to understand where your ICP spends time, what content they consume, and which influencers they follow — it answers these questions in seconds with aggregated data
- **Manual research first** (Reddit, G2, communities) when you need raw language, exact quotes, emotional context, and the "why" behind behavior
- **Best together**: Use SparkToro to identify which podcasts, subreddits, and websites matter, then go mine those sources manually for voice-of-customer language

### Key Queries to Run

**By competitor:**
- "People who follow @competitor" — reveals shared audience affinities
- "People who visit competitor.com" — shows what else they consume

**By audience description:**
- "People who frequently talk about [topic]" — finds audience behaviors
- "People whose bio contains [job title]" — profiles a role-based segment

**By your own audience:**
- "People who visit yourdomain.com" — understand your actual audience
- Compare against competitor audience profiles to find gaps

### What to Extract

| Data Type | What It Tells You | Use It For |
|-----------|------------------|------------|
| Top websites visited | Where your audience reads | Content partnerships, guest posting targets |
| Top podcasts | What they listen to | Podcast guesting, sponsorship decisions |
| Top YouTube channels | What they watch | Video content strategy, ad placements |
| Top subreddits | Where they discuss | Community participation, Reddit ad targeting |
| Search keywords | What they Google | SEO and content topic planning |
| AI prompt topics | What they ask AI tools | Emerging content opportunities |
| Social accounts followed | Who influences them | Influencer partnerships, co-marketing |
| Demographics | Who they are | Persona building, ad targeting |

### Source Weighting

SparkToro data is aggregated and anonymized — it shows patterns, not individual opinions. Treat it as:
- **High confidence** for behavioral data (what they visit, follow, search for)
- **Medium confidence** for demographic data (self-reported, may be incomplete)
- **Not a substitute** for qualitative research (doesn't capture language, emotions, or the "why")

### Limitations

- Free tier: 5 reports/month, shallow results (top 5–10)
- No public API — all research done through web interface
- Skews English-language, US-centric
- Shows what audiences do, not why — pair with qualitative sources

See [tools/integrations/sparktoro.md](https://github.com/coreyhaines31/marketingskills/blob/main/tools/integrations/sparktoro.md) for full tool details and pricing.

---

## Organizing Your Research

Use a simple tagging system across all sources:

| Tag | Meaning |
|-----|---------|
| `#pain` | A problem or frustration |
| `#trigger` | An event that prompted the search |
| `#outcome` | What success looks like |
| `#language` | Exact phrases worth using in copy |
| `#alternative` | Another solution they considered or use |
| `#objection` | Reason to hesitate or not buy |
| `#competitor` | Anything about a competing product |

Keep a running doc with columns: Source | Date | Quote | Tags | Notes

After 20-30 entries, patterns will emerge. Look for quotes that appear in multiple unrelated sources — those are your highest-confidence insights.

---

## Source Reliability and Confidence Scoring

Not all sources carry equal weight. Use this guide when assigning confidence labels.

### Source Weighting

| Source | Signal Strength | Bias to Note |
|--------|----------------|--------------|
| Customer interviews (unprompted) | Very high | Small sample; selection bias toward engaged customers |
| Win/loss interviews | High | Recent memory only; rationalization common |
| App store / G2 reviews | High | Skews toward strong opinions (love or hate) |
| Reddit / community posts | Medium-high | Skews technical, skeptical, vocal minorities |
| Support tickets | Medium | Skews toward problems; silent majority not represented |
| Survey (open-ended) | Medium | Primed by question framing |
| Survey (multiple choice) | Low-medium | Artifacts of the options you provided |
| NPS verbatims | Medium | Correlates with score; prompted by the survey moment |
| YouTube/TikTok comments | Medium | Skews toward engaged viewers; social performance |
| SparkToro audience data | Medium-high | Aggregated behavioral data; strong for "what" but not "why" |
| Job postings | Low-medium | Aspirational, not necessarily reflective of current pain |

### Confidence Labels in Practice

When presenting insights, lead with confidence:

```
[HIGH CONFIDENCE] Customers feel overwhelmed by manual reporting — appears in 12 of 20 interviews,
4 Reddit threads, and is the #1 complaint in 3-star G2 reviews. Consistent across SMB and mid-market.

[MEDIUM CONFIDENCE] Customers compare us to spreadsheets more than to direct competitors —
mentioned in 6 interviews and 3 Reddit threads, but not yet seen in review data.

[LOW CONFIDENCE] Enterprise buyers may have procurement concerns — mentioned by 2 interviewees
from companies 500+. Needs more signal before acting on it.
```

### Recency Window

- **Use as primary source**: Data from the last 12 months
- **Use with caution**: 12-24 months (product and market may have shifted)
- **Use only for baseline context**: 2+ years old

When a theme appears consistently across old and new data, that's a durable signal worth acting on.
