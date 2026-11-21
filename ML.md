# ML in DBMS-EL: study sheet

The DBMS side (schema, SQL, BCNF, views) is unchanged. ML replaces six hand-written decisions in the pipeline. Each decision is an interface in `backend/app/ml/` with the original logic kept as the baseline, and `backend/app/config.py` selects which one runs.

**Every number below comes from simulated users, not real usage.** There was no labelled data, so training and test data are generated. Treat the numbers as "works on the simulator", not as real-world accuracy.

---

## 1. Components at a glance

| # | Decision | Baseline (old logic) | ML option | Learning type | Default now |
|---|---|---|---|---|---|
| 1 | Group observations into clusters | Greedy centroid, cosine >= 0.50, 24 h window, 300 rows | Agglomerative, HDBSCAN | Unsupervised | agglomerative |
| 2 | Is a cluster worth a memory? | Always yes. Importance = 0.3 + 0.08 x size + 0.2 x recency | Gradient boosting on time-pattern features | Supervised | model |
| 3 | Detect routines | None | Rayleigh test (circular statistics) + binomial test | Statistical, no training | on |
| 4 | Forget unused memories | Never | Logistic regression: P(retrieved in next 14 days) | Supervised | model |
| 5 | Should this chat message get memories? | Always inject | Logistic regression on query embedding + similarities | Supervised | model |
| 6 | Order retrieved memories | SQL score 0.6 x cosine + 0.25 x importance + 0.15 x recency | Learned logistic regression, or pretrained cross-encoder | Supervised / pretrained | learned |

Embeddings are not new: `jinaai/jina-embeddings-v5-omni-small`, 1024-d, text and image in one space, runs locally on the GPU. Models are scikit-learn, saved to `data/models/*.joblib` (gitignored). A missing model file falls back to the baseline with a log warning.

---

## 2. Data

### 2.1 Generated with OpenAI (`scripts/synthetic/generate.py`)

| Dataset | Size | What it is | Output tokens |
|---|---|---|---|
| Topic catalog | 41 topics | 25 interests, 7 one-off trivia, 5 errands, 4 daily routines | n/a (hand-written) |
| Captions | 492 (41 topics x 12) | Screenshot-style caption, OCR text, tags, window title, per topic | 4 chunks of ~10 topics |
| Chat queries | 447 | 247 personal, 120 generic, 80 tricky | 6,346 / 2,490 / 4,943 |
| Memories | 41 | One consolidated memory text per topic | 1 call |

- **Tricky** queries share words with a topic but do not need it (e.g. "apple pie recipe" against Apple products research).
- **Personal** queries carry the list of relevant topic ids (some involve two topics).
- Model `gpt-6-luna`, flex tier (50% discount), retry with backoff, batch fallback after 6 consecutive failures. All calls finished on flex, so no batch job was used.
- A first captions request (all 41 topics at once) timed out after 15 min. It was split into 4 chunks, and the loop now splits automatically on timeout or truncation.

### 2.2 Usage simulator (`scripts/synthetic/sim.py`)

Fake users live 90 days. Ground truth is known, so models can be trained and tested.

| Behaviour | How it is simulated | "Worth a memory" label |
|---|---|---|
| Sustained interest | Sessions at 0.04-0.5 per day for weeks, until the end | Yes |
| Abandoned interest | Sessions at 0.1-0.6 per day for 12-30 days, then stops | Yes |
| Casual | 2-7 visits spread over the 90 days | No |
| Trivia | 1-3 one-off sessions, 20% chance of a one-day binge (6-12 observations) | No |
| Errand | A 1-3 day episode, 20% chance of monthly repeat | No |
| Routine | Near-daily (60-95% of days) at a fixed hour, 50% weekdays only | Yes |

- Time of day: 35% of interests are "habitual" (narrow hour, sd 0.7 h). Others are spread over the day.
- Chat queries ("hits") are also simulated: 0.12 per day for active interests, 0.01 after abandonment or for trivia.
- 24 training personas (activity 0.8 to 1.2) and 6 held-out test personas with different activity (0.6, 0.6, 0.8, 1.3, 1.3, 1.6). The test set deliberately differs from training.

| Dataset built from the simulator | Train rows | Test rows | Positive rate (train / test) | Snapshots (day) |
|---|---|---|---|---|
| Importance | 2,059 | 525 | 68% / 74% | 30, 45, 60, 75, 90 |
| Decay | 1,664 | 416 | 46% / 50% | 30, 45, 60, 75 |
| Gate | 1,872 | 810 | 26% / 25% need memory | pools from days 45-90 |
| Ranker | 700 queries | 288 queries | n/a | pools from days 45-90 |

---

## 3. Results

### 3.1 Importance (is this cluster worth a memory?)

Features come from timestamps only (`ml/features.py`): `n_obs`, `n_days`, `n_sessions`, `span_days`, `days_since_last`, `days_since_first`, `active_day_ratio`, `mean_gap_days`, `gap_cv`, `max_obs_one_day_share`, `n_days_last7`.

| Model | Test AUC | Accuracy @0.5 |
|---|---|---|
| **Gradient boosting (saved)** | **0.922** | 0.848 |
| Logistic regression | 0.916 | 0.848 |
| Baseline (cluster size only) | 0.855 | n/a |

- Top features (gradient boosting): `n_sessions` 0.53, `gap_cv` 0.10, `mean_gap_days` 0.06, `span_days` 0.06, `days_since_first` 0.06.
- Clusters under 0.5 stay "pending": no memory, no LLM call. They are reconsidered when they recur.
- Live check: a cluster spread over 20 days scored importance 0.95 (promoted). A cluster of 3 captures within 14 minutes scored 0.10 (pending).

### 3.2 Decay (will this memory be used again?)

Features: `age_days`, `days_since_update`, `importance`, `n_evidence`, `evidence_last14`, `days_since_last_evidence`, `hits_total`, `hits_last14`, `days_since_last_hit`.

| Model | Test AUC | Accuracy @0.5 |
|---|---|---|
| **Logistic regression (saved)** | **0.804** | 0.724 |
| Gradient boosting | 0.783 | 0.738 |
| Baseline (recency only) | 0.672 | n/a |

- Memories are archived (`archived_at`), not deleted, when P(use in 14 days) < 0.25. Only `auto` and `habit` memories at least 14 days old are eligible. New evidence un-archives a memory.
- Lowest-performing model of the set. It predicts a future event from limited signals.

### 3.3 Clustering

Test: 30 days of observations from the 6 held-out personas. Each observation is given a random caption embedding from its true topic, and true labels are the topics.

| Clusterer | ARI | Silhouette | Clusters found (true: 19.3) | Observations clustered |
|---|---|---|---|---|
| Greedy, current (0.50) | 0.331 | 0.575 | 60.7 | 91% |
| Greedy, tuned (0.35) | 0.551 | 0.241 | 27.7 | 98% |
| **Agglomerative (cosine distance 0.70, default)** | **0.664** | 0.257 | 28.0 | 98% |
| HDBSCAN, default settings | 0.156 | 0.824 | 92.3 | 93% |
| HDBSCAN, tuned (min size 5, PCA 10) | 0.290 | 0.360 | 28.7 | 85% |

Parameter sweep on the 8 training personas (to avoid tuning on the test set):

| Method | Best setting | ARI | Clusters |
|---|---|---|---|
| Agglomerative | distance 0.75 / 0.70 / 0.65 / 0.80 | 0.684 / 0.670 / 0.597 / 0.531 | 17 / 24 / 31 / 11 |
| Greedy | threshold 0.35 / 0.30 / 0.40 / 0.50 | 0.613 / 0.562 / 0.558 / 0.316 | 23 / 15 / 32 / 51 |
| HDBSCAN | min size 5, min samples 1, `eom`, PCA 10 | 0.355 | 26 |

- Silhouette is misleading here: HDBSCAN's 0.824 comes from many tiny tight clusters (92 clusters for 19 topics). ARI is the metric to quote.
- HDBSCAN's `cluster_selection_epsilon` is not used: sklearn 1.9 crashes with a value above 0.

### 3.4 Habit detection

No training. For each topic cluster, one angle per active day (average time of day), then:

| Test | What it checks | Threshold |
|---|---|---|
| Minimum evidence | Enough data | >= 5 observations on >= 4 distinct days |
| Rayleigh test | Time of day is concentrated (circular statistic, 23:00 is close to 01:00) | p <= 0.01 and concentration R >= 0.6 |
| Binomial test | Weekdays only or weekends only | p < 0.05, needs >= 5 days |

| Metric (6 test personas, last 30 days) | Value |
|---|---|
| Precision | 0.974 |
| Recall (of detectable habits) | 1.000 |
| True positives / false positives / missed | 38 / 1 / 0 |

- Output is a template sentence, with no LLM call. Example: "Habit: the user usually spends time on dbms notes most days at night (around 21:00-22:00)." Stored as a `source = 'habit'` memory.

### 3.5 Query gate (does this message need memory?)

Features: the query embedding (1024 values) + max similarity + mean of the top-3 similarities to the user's memories. Logistic regression with standardization (C = 0.05). Label is 1 only for a personal query whose topic exists in the user's memory pool.

| Method | Accuracy | AUC |
|---|---|---|
| Always inject (old behaviour) | 0.252 | n/a |
| Similarity threshold (0.34) | 0.901 | 0.945 |
| **Logistic regression gate** | 0.899 | **0.958** |

- Live check on the real database: "which laptop should I buy?" opened the gate. "what is 17 times 23" and "apple pie recipe" were blocked.
- Gate on = the old "inject the best 3 even if nothing is close" fallback is switched off.

### 3.6 Ranker (order the retrieved memories)

SQL fetches 20 candidates, the ranker reorders them, the top 5 are used. Test: 288 personal queries, each paired with a simulated memory pool.

| Ranking method | hit@1 | hit@3 | MRR |
|---|---|---|---|
| Current weights (0.6 / 0.25 / 0.15) | 0.760 | 0.878 | 0.835 |
| Cosine only | 0.920 | 0.986 | 0.955 |
| Cross-encoder only (`BAAI/bge-reranker-base`) | 0.823 | 0.965 | 0.888 |
| **Learned logistic regression (sim, importance, recency)** | 0.917 | 0.986 | 0.953 |

- Learned standardized weights: similarity +3.33, importance -0.66, recency +0.19.
- The cross-encoder score got near-zero weight (+0.03) when included, so the learned ranker leaves it out. That keeps query time fast and the reranker's 1 GB download optional.
- hit@k = a relevant memory appears in the top k. MRR = mean of 1 / (rank of the first relevant memory).

### 3.7 Train memories tab (human feedback loop)

| Item | Detail |
|---|---|
| Input | The user types example prompts. The app shows up to 8 memories it would pick, ignoring the gate |
| Feedback | Thumbs up or down per memory (`memory_feedback` table, one row per prompt and memory) |
| Retrain data | Simulated rows saved in `ranker_base.joblib` + votes at weight 5 |
| Triggers | Finish button, switching tabs or closing the app, backend startup if a vote is newer than `ranker.joblib` |
| Minimum | 4 votes with both a thumbs up and a thumbs down |
| Cost | Under 1 second, no GPU, hot-swapped with no restart |

---

## 4. Pros and cons

| Component | Pros | Cons |
|---|---|---|
| Importance model | Meets the "recurring vs one-off" requirement. Saves LLM calls on pending clusters. Explainable features. Beats the baseline by 0.07 AUC | Trained on simulated data. A new install makes few memories until patterns form. Needs 30 days of observations |
| Decay model | Keeps retrieval uncluttered. Archive, not delete, so it is reversible | Weakest model (AUC 0.80). Labels are simulated hits. Needs real retrieval history to be reliable |
| Agglomerative clustering | Best ARI (0.66). No training. Explainable distance threshold | Threshold 0.70 was tuned on simulated captions. O(n^2) on up to 3,000 observations |
| HDBSCAN | Finds the cluster count itself and marks noise | Worst ARI here (0.16 default, 0.29 tuned). Weak in 1024 dimensions. sklearn epsilon bug. Kept only as an option |
| Habit detection | No training and no labels. Statistical test with a p-value. Precision 0.97 | Needs 4+ days. One timezone assumption (local time). Template text is plain |
| Query gate | Blocks irrelevant injection (0.90 vs 0.25 accuracy). Cheap | Only slightly better than a plain similarity threshold. Accuracy is partly inflated by 75% negatives |
| Learned ranker | Beats the old hand-set weights (hit@1 0.92 vs 0.76). Retrains from user votes in under 1 s | About as good as cosine alone. Negative importance weight is likely a simulator artifact. Votes only dominate after dozens of prompts |
| Cross-encoder | Pretrained, no training. Adds a real ML baseline | Lower than cosine here. 1 GB download, slower per query |

---

## 5. Limitations and honest caveats

| Issue | Detail |
|---|---|
| Simulated data | All training and test data is generated. Real accuracy is unknown until real usage and votes accumulate |
| Circularity | The simulator and the models share the author's assumptions about behaviour. Held-out personas use different activity levels to reduce this |
| Leak found and fixed | The first importance model scored AUC 0.999 because `mean_hint` (LLM importance hint) separated the classes. The feature was removed, and the simulator gained overlap between low-rate interests and casual visits. Final AUC is 0.92 |
| Ranker assumption | Training pairs queries with pools where the relevant topic was recently active ("users ask about what they are doing now") |
| Small data | 41 topics, 492 captions, 447 queries. Test splits are a few hundred rows |
| Retention | The 30-day clustering window needs `users.retention_days >= 30` |
| Embedding dependence | Everything rests on the Jina embedding quality. Thresholds (0.70, 0.88) were measured on its similarity scale |

---

## 6. Parameters (`backend/app/config.py`)

| Parameter | Value | Meaning |
|---|---|---|
| `CLUSTERER` / `IMPORTANCE` / `DECAY` / `HABITS` / `GATE` / `RANKER` | agglomerative / model / model / True / model / learned | Which implementation runs |
| `CLUSTER_WINDOW_DAYS` | 30 | Look-back for ML clusterers (baseline greedy: 24 h, 300 rows) |
| `AGGLOMERATIVE_DISTANCE` | 0.70 | Cosine distance cut |
| `CLUSTER_MIN_OBSERVATIONS` | 2 | Smallest cluster |
| `IMPORTANCE_PROMOTE_THRESHOLD` | 0.5 | Below this a cluster stays pending |
| `DECAY_ARCHIVE_BELOW` / `DECAY_MIN_AGE_DAYS` | 0.25 / 14 | Archive rule |
| `HABIT_MIN_OBSERVATIONS` / `HABIT_MIN_DAYS` | 5 / 4 | Habit evidence |
| `HABIT_MAX_P_VALUE` / `HABIT_MIN_CONCENTRATION` | 0.01 / 0.6 | Rayleigh test limits |
| `RETRIEVAL_CANDIDATES` / `RETRIEVAL_TOP_K` | 20 / 5 | SQL candidates and memories used |
| `MEMORY_MERGE_SIMILARITY` / `MEMORY_DUPLICATE_SIMILARITY` | 0.70 / 0.88 | Unchanged from before |
| Ranker vote weight / minimum votes | 5 / 4 | In `ml/retrain.py` |

---

## 7. How to reproduce

| Step | Command |
|---|---|
| Generate data (OpenAI) | `uv run python scripts/synthetic/generate.py` (`--check`, `--import-batch FILE` for batch jobs) |
| Embed it | `uv run python scripts/synthetic/embed_data.py` |
| Train | `scripts/train_models.py importance` / `decay`, `scripts/train_retrieval.py gate` / `ranker` |
| Full report | `uv run python scripts/evaluate.py` (or one section: `clustering`, `habits`, `importance`, `decay`, `gate`, `ranker`) |

## 8. Code map

| Path | Role |
|---|---|
| `backend/app/ml/interfaces.py` | The six interfaces |
| `ml/clustering.py` | Greedy, agglomerative, HDBSCAN |
| `ml/importance.py`, `ml/decay.py`, `ml/habits.py` | Scorers and detector |
| `ml/features.py` | Time-pattern features shared by importance, decay and habits |
| `ml/gate.py`, `ml/ranking.py` | Gate, cross-encoder, learned ranker |
| `ml/retrain.py` | Refit from votes |
| `consolidation.py`, `lifecycle.py`, `retrieval.py` | Where the components are called |
| `scripts/synthetic/` | Catalog, generator, simulator, embedding script |
