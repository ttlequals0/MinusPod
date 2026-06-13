import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
} from 'recharts';
import CollapsibleSection from '../../components/CollapsibleSection';
import LoadingSpinner from '../../components/LoadingSpinner';
import { useThemeColors } from '../../hooks/useThemeColors';
import { getAdDistribution } from '../../api/feeds';
import { formatDuration } from '../settings/settingsUtils';
import type { AdDistribution, AdDistributionZone } from '../../api/types';

interface Props {
  slug: string;
}

function zoneLabel(center: number): string {
  const pct = Math.round(center * 100);
  if (center < 0.08) return `${pct}% pre-roll`;
  if (center > 0.92) return `${pct}% post-roll`;
  return `${pct}% mid-roll`;
}

function zoneTime(center: number, medianSeconds: number): string {
  if (!medianSeconds) return '';
  return ` (~${formatDuration(center * medianSeconds)})`;
}

function PanelBody({ data }: { data: AdDistribution }) {
  const theme = useThemeColors();
  const { episodesConsidered, medianDurationSeconds, totalEvents, buckets, bucketCount, zones } = data;

  const chartData = useMemo(() => {
    return buckets.map((count, i) => {
      // A bin spans [i/N, (i+1)/N]; highlight it when its span overlaps a zone
      // (not just when its center does -- robust to narrow zones / fewer bins).
      const low = i / bucketCount;
      const high = (i + 1) / bucketCount;
      const inZone = zones.some((z) => low <= z.high && high >= z.low);
      return {
        label: Math.round(low * 100),
        range: `${Math.round(low * 100)}-${Math.round(high * 100)}% of episode`,
        count,
        inZone,
      };
    });
  }, [buckets, bucketCount, zones]);

  if (episodesConsidered === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No processed episodes yet. Once this feed has processed episodes, the
        chart shows where in each episode ads have historically been cut.
      </p>
    );
  }

  if (totalEvents === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No ads have been cut across this feed's {episodesConsidered} processed
        episodes yet.
      </p>
    );
  }

  const medianMin = Math.round(medianDurationSeconds / 60);
  const avgPerEp = (totalEvents / episodesConsidered).toFixed(1);

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        <span className="text-foreground font-medium">{episodesConsidered}</span> episodes
        {medianMin > 0 && <> &middot; median <span className="text-foreground font-medium">{medianMin} min</span></>}
        {' '}&middot; <span className="text-foreground font-medium">{avgPerEp}</span> ads/ep
      </p>

      <div className="h-44 -ml-2">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={theme.border} vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 11, fill: theme.muted }}
              tickLine={false}
              axisLine={{ stroke: theme.border }}
              interval={Math.max(0, Math.floor(bucketCount / 5) - 1)}
              tickFormatter={(v) => `${v}%`}
            />
            <YAxis
              allowDecimals={false}
              width={28}
              tick={{ fontSize: 11, fill: theme.muted }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              cursor={{ fill: theme.border, opacity: 0.3 }}
              contentStyle={{
                backgroundColor: theme.card,
                border: `1px solid ${theme.border}`,
                borderRadius: 8,
                fontSize: 12,
                color: theme.foreground,
              }}
              labelFormatter={(_label, payload) => payload?.[0]?.payload?.range ?? ''}
              formatter={(value) => {
                const n = Number(value);
                return [`${n} cut${n === 1 ? '' : 's'}`, ''];
              }}
            />
            <Bar dataKey="count" radius={[3, 3, 0, 0]} isAnimationActive={false}>
              {chartData.map((d, i) => (
                <Cell
                  key={i}
                  fill={theme.primary}
                  fillOpacity={d.inZone ? 1 : 0.32}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {zones.length > 0 ? (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
            Learned ad-break zones
          </p>
          <ul className="space-y-1">
            {zones.map((z: AdDistributionZone) => (
              <li key={`${z.low}-${z.high}`} className="flex items-center gap-2 text-sm">
                <span className="inline-block w-2.5 h-2.5 rounded-sm bg-primary shrink-0" />
                <span className="text-foreground font-medium">
                  {zoneLabel(z.center)}
                </span>
                <span className="text-muted-foreground">
                  {zoneTime(z.center, medianDurationSeconds)} &middot; {z.support}/{episodesConsidered} episodes
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          No consistent ad-break zone yet. Zones appear once enough recent
          episodes place an ad at a similar point in the episode.
        </p>
      )}
    </div>
  );
}

function PodcastAdDistributionPanel({ slug }: Props) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['ad-distribution', slug],
    queryFn: () => getAdDistribution(slug),
    enabled: !!slug,
  });

  return (
    <div className="mb-6">
      <CollapsibleSection
        title="Ad Distribution"
        subtitle="Where ads have historically been cut across this feed's episodes"
        defaultOpen={false}
        storageKey={`feed-ad-distribution-${slug}`}
      >
        {isLoading && <LoadingSpinner size="sm" className="my-2" />}
        {error && (
          <p className="text-sm text-muted-foreground">
            Could not load ad distribution for this feed.
          </p>
        )}
        {data && <PanelBody data={data} />}
      </CollapsibleSection>
    </div>
  );
}

export default PodcastAdDistributionPanel;
