import CollapsibleSection from '../../components/CollapsibleSection';
import { Check } from 'lucide-react';
import { useTheme } from '../../context/ThemeContext';
import { THEME_GROUPS, GROUPED_THEMES } from '../../themes';
import { focusRing } from '../../components/fieldStyles';
import SideCutoutToggle from './SideCutoutToggle';

function AppearanceSection() {
  const { theme, themeId, setThemeId } = useTheme();

  return (
    <CollapsibleSection title="Appearance" subtitle="Color theme for the interface">
      <div className="space-y-4">
        {THEME_GROUPS.map((group) => {
          const groupThemes = GROUPED_THEMES.get(group);
          if (!groupThemes?.length) return null;
          return (
            <div key={group}>
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
                {group}
              </p>
              <div className="grid grid-cols-2 gap-2">
                {groupThemes.map((t) => {
                  const isActive = t.id === themeId;
                  const only = t.light === null && t.dark ? 'dark' : t.dark === null && t.light ? 'light' : null;
                  const palette = theme === 'dark' ? (t.dark ?? t.light) : (t.light ?? t.dark);
                  const bgColor = palette
                    ? `hsl(${palette.background})`
                    : theme === 'dark' ? 'hsl(213 10% 17%)' : 'hsl(210 17% 95%)';
                  const cardColor = palette
                    ? `hsl(${palette.card})`
                    : theme === 'dark' ? 'hsl(210 8% 25%)' : 'hsl(0 0% 100%)';
                  const primaryColor = palette ? `hsl(${palette.primary})` : t.accentColor;
                  return (
                    <button
                      key={t.id}
                      type="button"
                      aria-pressed={isActive}
                      onClick={() => setThemeId(t.id)}
                      className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm text-left transition-colors ${
                        isActive
                          ? 'border-primary bg-primary/10 text-foreground'
                          : 'border-border bg-background text-foreground hover:bg-accent'
                      } ${focusRing}`}
                    >
                      <span
                        aria-hidden="true"
                        data-testid={`theme-swatch-${t.id}`}
                        className="inline-flex h-3.5 w-9 shrink-0 overflow-hidden rounded border border-border/80"
                      >
                        <span className="flex-1" style={{ backgroundColor: bgColor }} />
                        <span className="flex-1" style={{ backgroundColor: cardColor }} />
                        <span className="flex-1" style={{ backgroundColor: primaryColor }} />
                      </span>
                      <span className="truncate">
                        {t.label}
                        {only && (
                          <span
                            className="text-muted-foreground text-xs ml-1"
                            title={`${only === 'dark' ? 'Light' : 'Dark'} mode uses the default ${only === 'dark' ? 'light' : 'dark'} theme`}
                          >
                            ({only} only)
                          </span>
                        )}
                      </span>
                      {isActive && (
                        <Check className="w-4 h-4 text-primary shrink-0 ml-auto" aria-hidden="true" />
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
      <SideCutoutToggle />
    </CollapsibleSection>
  );
}

export default AppearanceSection;
