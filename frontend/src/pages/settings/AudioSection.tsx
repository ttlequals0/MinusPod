import CollapsibleSection from '../../components/CollapsibleSection';
import NumberInput from '../../components/NumberInput';
import ToggleSwitch from '../../components/ToggleSwitch';
import ReplacementAudioField from './ReplacementAudioField';
import { selectBase } from '../../components/fieldStyles';

interface AudioSectionProps {
  audioBitrate: string;
  onAudioBitrateChange: (bitrate: string) => void;
  audioEncoderCompressionLevel: string;
  onAudioEncoderCompressionLevelChange: (level: string) => void;
  audioReplacementSoundEnabled: boolean;
  onAudioReplacementSoundEnabledChange: (enabled: boolean) => void;
  audioMp3StreamCopyEnabled: boolean;
  onAudioMp3StreamCopyEnabledChange: (enabled: boolean) => void;
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
  audioEncoderCompressionLevel,
  onAudioEncoderCompressionLevelChange,
  audioReplacementSoundEnabled,
  onAudioReplacementSoundEnabledChange,
  audioMp3StreamCopyEnabled,
  onAudioMp3StreamCopyEnabledChange,
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
            className={`w-full ${selectBase}`}
          >
            <option value="64k">64 kbps</option>
            <option value="96k">96 kbps</option>
            <option value="128k">128 kbps (recommended)</option>
            <option value="192k">192 kbps</option>
            <option value="256k">256 kbps</option>
          </select>
          <p className="mt-1 text-sm text-muted-foreground">
            Applies when the episode is re-encoded.
          </p>
        </div>

        <div>
          <label htmlFor="audioEncoderCompressionLevel" className="block text-sm font-medium text-foreground mb-2">
            Encoder Compression Level
          </label>
          <select
            id="audioEncoderCompressionLevel"
            value={audioEncoderCompressionLevel}
            onChange={(e) => onAudioEncoderCompressionLevelChange(e.target.value)}
            className={`w-full ${selectBase}`}
          >
            <option value="default">Default</option>
            {Array.from({ length: 10 }, (_, i) => String(i)).map((level) => (
              <option key={level} value={level}>{level}</option>
            ))}
          </select>
          <p className="mt-1 text-sm text-muted-foreground">
            libmp3lame compression level. Lower is slower and higher quality; 7 is about twice as fast as default for speech.
          </p>
        </div>

        <div>
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={audioMp3StreamCopyEnabled}
              onChange={onAudioMp3StreamCopyEnabledChange}
              ariaLabel="MP3 stream copy"
            />
            <span className="text-sm font-medium text-foreground">MP3 stream copy</span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground ml-14">
            Tries compatible MP3 cuts; otherwise re-encodes.
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
            Even out quiet and loud passages.
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
              className={`w-full ${selectBase}`}
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

        <div>
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={audioReplacementSoundEnabled}
              onChange={onAudioReplacementSoundEnabledChange}
              ariaLabel="Replacement sound"
            />
            <span className="text-sm font-medium text-foreground">Replacement sound</span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground ml-14">
            Insert a sound where audio is removed.
          </p>
        </div>

        <ReplacementAudioField />

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
