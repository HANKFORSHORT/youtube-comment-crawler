# YouTube Comment Collector

Collects comments from YouTube videos you choose and **appends** them to a CSV
file with just three columns: `ID`, `parent_ID`, `text`. Built for putting
together a Vietnamese–English code-switching dataset.

## Setup

Requires Python 3.9+.

```bash
pip install -r requirements.txt
```

### Get an API key
1. Go to <https://console.cloud.google.com/> and create (or pick) a project.
2. **APIs & Services → Library →** enable **YouTube Data API v3**.
3. **APIs & Services → Credentials → Create credentials → API key.**
4. Paste it into `config.yaml` (`api_key`), or set an environment variable
   (this overrides the config):
   ```bash
   export YOUTUBE_API_KEY="your-key"        # macOS / Linux
   setx YOUTUBE_API_KEY "your-key"          # Windows (reopen terminal after)
   ```

Don't commit your key to Git.

## Usage

```bash
# 1) Links listed in config.yaml, or – if none – paste them when prompted
python youtube_collector.py

# 2) Links as arguments
python youtube_collector.py "https://www.youtube.com/watch?v=XXXXXXXXXXX" https://youtu.be/YYYYYYYYYYY

# 3) Links from a text file (one per line, # for comments)
python youtube_collector.py --urls-file links.txt
```

Accepted formats: `watch?v=`, `youtu.be/`, `/shorts/`, `/embed/`, `/live/`, or a bare video ID.

## How "add-up, don't replace" works

- Every comment has a unique `ID`.
- On each run the IDs already in the CSV are loaded, only comments with unseen IDs
  are appended, and existing rows are never touched.
- Progress is saved after each video, and also if the API quota runs out mid-way.
- Re-running on the same video fetches further comments until `max_comments_per_video`
  *new* ones are added. For a different slice of a video, switch `order` between
  `relevance` and `time`.

## Output format (`data/comments.csv`)

```csv
ID,parent_ID,text
UgxABC...,,"Bài này hay quá, the beat is so chill luôn"
UgxABC....DEF,UgxABC...,"đúng rồi bro"
```

- `ID` is the YouTube comment ID.
- `parent_ID` is empty for top-level comments, or the parent's `ID` for replies.
- `text` is the comment text. Multi-line comments and commas are quoted per standard CSV.
- The file is UTF-8 (with a BOM when created, so Excel shows Vietnamese correctly).
- Nothing else is stored: no author names, video info, like counts or timestamps.
  Keeping only IDs and text means the dataset can be re-linked to the original
  comments through the API, which is the point of this minimal format.
- `only_code_switch_candidates: true` still filters at collection time using a rough
  heuristic (Vietnamese words + common English words in the same comment). It will
  miss cases and give false positives, so treat it as a pre-filter, not ground truth.
  The flag itself is not written to the CSV.

## API quota

The default quota is 10,000 units/day. `commentThreads.list` and `comments.list`
cost 1 unit per page (up to 100 comments), so collecting 100k comments costs
roughly 1,000+ units. If you hit the limit, the script saves and stops; run it
again the next day.

## Notes on using the data

- Check YouTube's API Services Terms and Developer Policies before publishing or
  redistributing collected comments. Releasing comment IDs plus this script is often
  safer than releasing raw text.
- Consider removing @mentions, URLs, and other personal info before sharing.
