import CollapsibleSection from '../../components/CollapsibleSection';

interface AudioSectionProps {
  audioBitrate: string;
  onAudioBitrateChange: (bitrate: string) => void;
}

function AudioSection({ audioBitrate, onAudioBitrateChange }: AudioSectionProps) {
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

        <div className="pt-3 border-t border-border">
          <h3 className="text-sm font-medium text-foreground mb-1">Audio Analysis</h3>
          <p className="text-sm text-muted-foreground">
            Volume and transition analysis runs automatically on every episode. Detects volume anomalies and abrupt loudness transitions that indicate dynamically inserted ads. Audio signals are included as context in the AI detection prompt.
          </p>
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default AudioSection;
