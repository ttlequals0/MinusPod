# Finding Feeds & Usage

[< Docs index](README.md) | [Project README](../README.md)

---

## Finding Podcast RSS Feeds

MinusPod includes a built-in podcast search powered by [PodcastIndex.org](https://podcastindex.org). Search by name directly from the Add Feed page. To enable search, get free API credentials at [api.podcastindex.org/signup](https://api.podcastindex.org/signup) and add them in Settings > Podcast Search.

![Podcast search on the Add Feed page, Dracula dark theme](images/podcast-search-dark.png)

You can also find RSS feeds manually:

1. **Podcast website** - Look for "RSS" link in footer or subscription options
2. **Apple Podcasts** - Search on [podcastindex.org](https://podcastindex.org) using the Apple Podcasts URL
3. **Spotify-exclusive** - Not available (Spotify doesn't expose RSS feeds)
4. **Hosting platforms** - Common patterns:
   - Libsyn: `https://showname.libsyn.com/rss`
   - Spreaker: `https://www.spreaker.com/show/{id}/episodes/feed`
   - Omny: Check page source for `omnycontent.com` URLs

## Usage

Add your modified feed URL to any podcast app:
```
http://your-server:8000/your-feed-slug
```

The feed URL is shown in the web UI and can be copied to clipboard.

### Audiobookshelf

If using [Audiobookshelf](https://www.audiobookshelf.org/) as your podcast client, its SSRF protection will block requests to MinusPod when running on a local/private network. Add your MinusPod hostname or IP to Audiobookshelf's whitelist:

```
SSRF_REQUEST_FILTER_WHITELIST=minuspod.local,192.168.1.100
```

This is a comma-separated list of domains excluded from Audiobookshelf's SSRF filter. See [Audiobookshelf Security docs](https://www.audiobookshelf.org/docs/#security) for details.

---

[< Docs index](README.md) | [Project README](../README.md)
