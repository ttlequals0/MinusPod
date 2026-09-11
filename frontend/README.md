# Frontend Development Guide

## Contents

- [Quick Start](#quick-start)
- [Technology Stack](#technology-stack)
- [Project Structure](#project-structure)
- [Key Patterns](#key-patterns)
- [Development Tips](#development-tips)
- [Building for Production](#building-for-production)

## Quick Start

```bash
# Install dependencies
npm ci

# Start dev server (proxies to backend at localhost:8000)
npm run dev

# Build for production
npm run build

# Run linter
npm run lint

# Run tests
npm run test
```

## Technology Stack

- **React 19.2.8** - UI framework
- **TypeScript 6.0.3** - Type safety
- **Vite 8.2.2** - Build tool
- **TanStack Query 5.102.8** - Server state management
- **Tailwind CSS 4.3.3** - Styling
- **Lucide React 1.33.0** - Icons
- **React Router 8.3.0** - Routing
- **Fetch API** - HTTP client

## Project Structure

```
src/
|-- api/              # API client and types
|   |-- auth.ts       # Authentication endpoints
|   |-- client.ts     # Base fetch API wrapper
|   |-- feeds.ts      # Feed/episode endpoints
|   |-- history.ts    # History endpoints
|   |-- patterns.ts   # Pattern endpoints
|   |-- search.ts     # Search endpoints
|   |-- settings.ts   # Settings endpoints
|   |-- sponsors.ts   # Sponsor endpoints
|   `-- types.ts      # TypeScript interfaces
|-- components/       # Reusable components
|   |-- EpisodeList.tsx
|   |-- FeedCard.tsx
|   |-- FeedListItem.tsx
|   |-- GlobalStatusBar.tsx
|   |-- ChunkLoadRecovery.tsx
|   |-- Layout.tsx
|   |-- LoadingSpinner.tsx
|   |-- PatternDetailModal.tsx
|   `-- AdEditor.tsx
|-- context/          # React contexts
|   |-- AuthContext.tsx
|   `-- ThemeContext.tsx
|-- hooks/            # Custom hooks
|-- pages/            # Route pages
|   |-- AddFeed.tsx
|   |-- Dashboard.tsx
|   |-- EpisodeDetail.tsx
|   |-- FeedDetail.tsx
|   |-- HistoryPage.tsx
|   |-- Login.tsx
|   |-- PatternsPage.tsx
|   |-- patterns/          # Ad review and unresolved corrections
|   |-- Search.tsx
|   `-- Settings.tsx
|-- App.tsx           # Route configuration
|-- main.tsx          # Entry point
`-- index.css         # Global styles
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

Use the semantic theme tokens defined in `index.css`:

```tsx
<div className="bg-background text-foreground border-border">
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
