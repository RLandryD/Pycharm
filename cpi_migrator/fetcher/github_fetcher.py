"""
fetcher/github_fetcher.py

Fetches SAP integration recipes directly from the public GitHub repository:
  https://github.com/SAP/apibusinesshub-integration-recipes

Uses raw file downloads (no API rate limits for file content).
Falls back to the static catalog in hub_fetcher.py if GitHub is unreachable.

Repo structure:
  Recipes/
    for/<ArtifactType>/
      readme.md           ← index of all recipes
    <Topic>/
      <RecipeName>/
        readme.md         ← description
        *.zip             ← actual iFlow package (when present)
"""
from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

GITHUB_RAW  = "https://raw.githubusercontent.com/SAP/apibusinesshub-integration-recipes/master"
GITHUB_API  = "https://api.github.com/repos/SAP/apibusinesshub-integration-recipes/contents"
RECIPES_DIR = "Recipes"


class GitHubFetcher:
    """
    Fetches SAP integration recipes from the public GitHub repo.
    No authentication required for public repos.
    Uses raw.githubusercontent.com for file content (no rate limits).
    Uses api.github.com only for directory listings (rate-limited to 60/hr unauthenticated).
    """

    def __init__(self, cache_dir: Optional[Path] = None, github_token: str = ""):
        self.cache_dir = cache_dir or (Path.home() / ".cpi_migrator" / "github_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "CPI-Migration-Scaffolder/1.0",
        })
        if github_token:
            self.session.headers["Authorization"] = f"Bearer {github_token}"

    # ── Index ─────────────────────────────────────────────────────────

    def fetch_recipe_index(self) -> list[dict]:
        """
        Returns a list of recipe metadata dicts from the cached or live index.
        Each dict: {name, topic, description, has_zip, raw_url, local_path}
        """
        index_cache = self.cache_dir / "recipe_index.json"
        if index_cache.exists():
            import json, time
            data = json.loads(index_cache.read_text())
            # Use cached index if < 24h old
            if time.time() - data.get("fetched_at", 0) < 86400:
                logger.debug("Using cached GitHub recipe index")
                return data.get("recipes", [])

        logger.info("Fetching recipe index from GitHub…")
        recipes = []
        try:
            recipes = self._scan_recipes_directory()
        except Exception as exc:
            logger.warning("GitHub index fetch failed: %s — using cached/static", exc)
            if index_cache.exists():
                import json
                return json.loads(index_cache.read_text()).get("recipes", [])

        # Cache the index
        import json, time
        index_cache.write_text(json.dumps({
            "fetched_at": time.time(),
            "recipes": recipes,
        }, indent=2), "utf-8")
        logger.info("Cached %d recipes from GitHub", len(recipes))
        return recipes

    def _scan_recipes_directory(self) -> list[dict]:
        """Walk the Recipes/ directory tree via GitHub API."""
        recipes = []
        try:
            url  = f"{GITHUB_API}/{RECIPES_DIR}"
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 403:
                logger.warning("GitHub API rate limit hit — using raw readme index")
                return self._parse_readme_index()
            resp.raise_for_status()
            topics = [item for item in resp.json()
                      if item["type"] == "dir" and item["name"] != "for"]
        except Exception:
            return self._parse_readme_index()

        for topic in topics[:20]:  # cap to avoid rate limit
            try:
                t_resp = self.session.get(topic["url"], timeout=10)
                if t_resp.status_code == 403:
                    break
                t_resp.raise_for_status()
                for item in t_resp.json():
                    if item["type"] == "dir":
                        recipe = self._build_recipe_meta(
                            name=item["name"],
                            topic=topic["name"],
                            api_url=item["url"],
                        )
                        if recipe:
                            recipes.append(recipe)
            except Exception:
                continue

        return recipes

    def _build_recipe_meta(self, name: str, topic: str, api_url: str) -> Optional[dict]:
        """Build metadata dict for one recipe directory."""
        try:
            resp = self.session.get(api_url, timeout=10)
            if resp.status_code == 403:
                return None
            resp.raise_for_status()
            files    = resp.json()
            has_zip  = any(f["name"].endswith(".zip") for f in files)
            zip_url  = next((f["download_url"] for f in files
                             if f["name"].endswith(".zip")), None)
            readme   = next((f for f in files if f["name"].lower() == "readme.md"), None)
            desc     = ""
            if readme:
                try:
                    r = self.session.get(readme["download_url"], timeout=10)
                    lines = r.text.splitlines()
                    desc  = next((l.strip("# ").strip() for l in lines if l.strip()), "")
                except Exception:
                    pass
            return {
                "name":        name,
                "topic":       topic,
                "description": desc,
                "has_zip":     has_zip,
                "zip_url":     zip_url,
                "api_url":     api_url,
            }
        except Exception:
            return None

    def _parse_readme_index(self) -> list[dict]:
        """
        Fallback: parse the main readme.md which lists all recipes.
        Works without GitHub API (no rate limit).
        """
        url  = f"{GITHUB_RAW}/{RECIPES_DIR}/readme.md"
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            return []

        recipes = []
        for line in resp.text.splitlines():
            line = line.strip()
            if line.startswith("|") and "](http" in line:
                # Parse markdown table rows with links
                parts = [p.strip() for p in line.split("|") if p.strip()]
                for part in parts:
                    if "](http" in part:
                        try:
                            name = part.split("](")[0].lstrip("[")
                            url_ = part.split("](")[1].rstrip(")")
                            recipes.append({
                                "name":        name,
                                "topic":       "General",
                                "description": name.replace("-", " ").replace("_", " "),
                                "has_zip":     False,
                                "zip_url":     None,
                                "api_url":     url_,
                            })
                        except Exception:
                            continue
        return recipes

    # ── Download ─────────────────────────────────────────────────────

    def download_recipe(self, recipe: dict) -> Optional[Path]:
        """
        Download a recipe .zip and unpack to cache_dir/recipes/<name>/.
        Returns the local path, or None if no zip available.
        """
        if not recipe.get("zip_url"):
            logger.debug("No zip for recipe %s", recipe["name"])
            return None

        dest = self.cache_dir / "recipes" / recipe["name"]
        if dest.exists():
            logger.debug("Already cached: %s", dest)
            return dest

        logger.info("Downloading recipe %s…", recipe["name"])
        try:
            resp = requests.get(recipe["zip_url"], timeout=30)
            resp.raise_for_status()
            dest.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                zf.extractall(dest)
            logger.info("Downloaded %s → %s", recipe["name"], dest)
            return dest
        except Exception as exc:
            logger.warning("Failed to download %s: %s", recipe["name"], exc)
            return None

    # ── Search ────────────────────────────────────────────────────────

    def search_recipes(
        self,
        keywords: list[str],
        sender_adapter: str = "",
        receiver_adapter: str = "",
        top_n: int = 5,
    ) -> list[tuple[int, dict]]:
        """
        Search the recipe index by keyword + adapter type.
        Returns list of (score, recipe_dict) sorted by relevance.
        """
        index   = self.fetch_recipe_index()
        scored  = []
        kw_set  = {k.lower() for k in keywords if len(k) > 2}
        kw_set.update({sender_adapter.lower(), receiver_adapter.lower()})

        for recipe in index:
            text  = (recipe["name"] + " " + recipe.get("description", "") +
                     " " + recipe.get("topic", "")).lower()
            score = sum(2 for kw in kw_set if kw in text)
            if score > 0:
                scored.append((score, recipe))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_n]
