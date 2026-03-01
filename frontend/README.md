# Frontend Development Guide

## Quick Start

```bash
# Install dependencies
npm install

# Start dev server (proxies to backend at localhost:8000)
npm run dev

# Build for production
npm run build

# Run linter
npm run lint
```

## Technology Stack

- **React 18** - UI framework
- **TypeScript 5** - Type safety
- **Vite 5** - Build tool
- **TanStack Query 5** - Server state management
- **Tailwind CSS 3** - Styling
- **Lucide React** - Icons
- **React Router 6** - Routing
- **Axios** - HTTP client

## Project Structure

```
src/
├── api/              # API client and types
│   ├── auth.ts       # Authentication endpoints
│   ├── client.ts     # Base axios wrapper
│   ├── feeds.ts      # Feed/episode endpoints
│   ├── history.ts    # History endpoints
│   ├── patterns.ts   # Pattern endpoints
│   ├── search.ts     # Search endpoints
│   ├── settings.ts   # Settings endpoints
│   ├── sponsors.ts   # Sponsor endpoints
│   └── types.ts      # TypeScript interfaces
├── components/       # Reusable components
│   ├── EpisodeList.tsx
│   ├── FeedCard.tsx
│   ├── FeedListItem.tsx
│   ├── GlobalStatusBar.tsx
│   ├── Layout.tsx
│   ├── LoadingSpinner.tsx
│   ├── PatternDetailModal.tsx
│   └── AdEditor.tsx
├── context/          # React contexts
│   ├── AuthContext.tsx
│   └── ThemeContext.tsx
├── hooks/            # Custom hooks
│   └── useTranscriptKeyboard.ts
├── pages/            # Route pages
│   ├── AddFeed.tsx
│   ├── Dashboard.tsx
│   ├── EpisodeDetail.tsx
│   ├── FeedDetail.tsx
│   ├── HistoryPage.tsx
│   ├── Login.tsx
│   ├── PatternsPage.tsx
│   ├── Search.tsx
│   └── Settings.tsx
├── App.tsx           # Route configuration
├── main.tsx          # Entry point
└── index.css         # Global styles
```

## Key Patterns

### Data Fetching

Use TanStack Query for all API calls:

```tsx
const { data, isLoading, error } = useQuery({
    queryKey: ['feeds'],
    queryFn: () => fetchFeeds(),
    staleTime: 30000, // 30 seconds
});
```

### Mutations

```tsx
const mutation = useMutation({
    mutationFn: (data) => updateFeed(slug, data),
    onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ['feeds'] });
    },
});
```

### Theme Support

Use Tailwind's dark mode classes:

```tsx
<div className="bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100">
    Content
</div>
```

Theme is managed via `ThemeContext` and persisted to localStorage.

### Path Alias

Use `@/` for absolute imports:

```tsx
import { fetchFeeds } from '@/api/feeds';
import { LoadingSpinner } from '@/components/LoadingSpinner';
```

## Development Tips

1. **API Proxy** - Dev server proxies `/api` and `/health` to `localhost:8000`
2. **Hot Reload** - Changes auto-refresh, but may need manual refresh for API changes
3. **Type Generation** - Types in `api/types.ts` should match backend schemas
4. **Mobile Testing** - Use Chrome DevTools device mode

## Building for Production

```bash
npm run build
```

Output goes to `../static/ui/` (served by Flask at `/ui/`).

The build process:
1. TypeScript compilation (`tsc`)
2. Vite bundling with tree-shaking
3. Output to `static/ui/` directory
