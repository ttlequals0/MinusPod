import CollapsibleSection from '../../components/CollapsibleSection';
import { useTheme } from '../../context/ThemeContext';
import { THEME_GROUPS, GROUPED_THEMES } from '../../themes';

function AppearanceSection() {
  const { themeId, setThemeId } = useTheme();

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
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                {groupThemes.map((t) => {
                  const isActive = t.id === themeId;
                  const isDarkOnly = t.light === null && t.dark !== null;
                  return (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => setThemeId(t.id)}
                      className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm text-left transition-colors ${
                        isActive
                          ? 'border-primary bg-primary/10 text-foreground'
                          : 'border-border bg-background text-foreground hover:bg-accent'
                      }`}
                    >
                      <span
                        className="w-3 h-3 rounded-full shrink-0"
                        style={{ backgroundColor: t.accentColor }}
                      />
                      <span className="truncate">
                        {t.label}
                        {isDarkOnly && (
                          <span className="text-muted-foreground text-xs ml-1">(dark only)</span>
                        )}
                      </span>
                      {isActive && (
                        <svg className="w-4 h-4 text-primary shrink-0 ml-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </CollapsibleSection>
  );
}

export default AppearanceSection;
