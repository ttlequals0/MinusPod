import CollapsibleSection from '../../components/CollapsibleSection';
import NumberInput from '../../components/NumberInput';
import ToggleSwitch from '../../components/ToggleSwitch';

interface AudioSectionProps {
  audioBitrate: string;
  onAudioBitrateChange: (bitrate: string) => void;
  audioNormalizeEnabled: boolean;
  onAudioNormalizeEnabledChange: (enabled: boolean) => void;
  audioNormalizeIntensity: string;
  onAudioNormalizeIntensityChange: (intensity: string) => void;
  maxAudioDownloadMb: number;
  onMaxAudioDownloadMbChange: (mb: number) => void;
}

function AudioSection({
  audioBitrate,
  onAudioBitrateChange,
  audioNormalizeEnabled,
  onAudioNormalizeEnabledChange,
  audioNormalizeIntensity,
  onAudioNormalizeIntensityChange,
  maxAudioDownloadMb,
  onMaxAudioDownloadMbChange,
}: AudioSectionProps) {
  return (
    <CollapsibleSection title="Audio">
      <div className="space-y-4">
        <div>
          <label htmlFor="audioBitrate" className="block text-sm font-medium text-foreground mb-2">
            Output Bitrate
          </label>
          <select
            id="audioBitrate"
            value={audioBitrate}
            onChange={(e) => onAudioBitrateChange(e.target.value)}
            className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring"
          >
            <option value="64k">64 kbps - Smallest file size</option>
            <option value="96k">96 kbps - Good for speech</option>
            <option value="128k">128 kbps - Standard quality (recommended)</option>
            <option value="192k">192 kbps - High quality</option>
            <option value="256k">256 kbps - Maximum quality</option>
          </select>
          <p className="mt-1 text-sm text-muted-foreground">
            Higher bitrates produce better audio quality but larger file sizes
          </p>
        </div>

        <div>
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={audioNormalizeEnabled}
              onChange={onAudioNormalizeEnabledChange}
              ariaLabel="Audio Leveling"
            />
            <span className="text-sm font-medium text-foreground">Audio Leveling (loudness normalization)</span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground ml-14">
            Runs a second ffmpeg pass (dynaudnorm) on the final audio to even out
            the volume between quiet and loud passages, so the episode plays at a
            more consistent level. Adds ~3-5s per episode.
          </p>
        </div>

        {audioNormalizeEnabled && (
          <div>
            <label htmlFor="audioNormalizeIntensity" className="block text-sm font-medium text-foreground mb-2">
              Normalization Intensity
            </label>
            <select
              id="audioNormalizeIntensity"
              value={audioNormalizeIntensity}
              onChange={(e) => onAudioNormalizeIntensityChange(e.target.value)}
              className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring"
            >
              <option value="gentle">Gentle - Light leveling, preserves dynamics</option>
              <option value="normal">Normal - Balanced leveling (recommended)</option>
              <option value="aggressive">Aggressive - Strong leveling</option>
              <option value="extreme">Extreme - Heavy compression, very even level</option>
              <option value="maximum">Maximum - Flattest possible (may add slight pumping)</option>
            </select>
            <p className="mt-1 text-sm text-muted-foreground">
              Stronger settings flatten harder but reduce natural dynamics; Extreme and Maximum add compression on top.
            </p>
          </div>
        )}

        <div className="pt-4 border-t border-border">
          <label htmlFor="maxAudioDownloadMb" className="block text-sm font-medium text-foreground mb-2">
            Max episode download (MB)
          </label>
          <div className="flex items-center gap-3">
            <NumberInput
              id="maxAudioDownloadMb"
              value={maxAudioDownloadMb}
              min={1}
              max={1048576}
              fallback={500}
              parse={(s) => parseInt(s, 10)}
              onCommit={(mb) => {
                if (mb !== maxAudioDownloadMb) onMaxAudioDownloadMbChange(mb);
              }}
            />
            <span className="text-sm text-muted-foreground">MB (minimum 1)</span>
          </div>
          <p className="mt-2 text-sm text-muted-foreground">
            Episode downloads over this size fail the episode instead of processing. Default 500 MB.
          </p>
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default AudioSection;
