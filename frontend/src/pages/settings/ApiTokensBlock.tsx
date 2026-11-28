import { useId, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { API_TOKENS_QUERY_KEY, ApiToken, createApiToken, listApiTokens, revokeApiToken } from '../../api/apiTokens';
import { getErrorMessage } from '../../api/client';
import CopyButton from '../../components/CopyButton';
import { DemoDisabled } from '../../components/DemoDisabled';
import { ConfirmModal, Modal, dialogFooterBtn } from '../../components/Modal';
import { btnBase, btnDestructiveOutline, btnOutline, btnPrimary } from '../../components/buttonStyles';
import { inputBase, labelBase } from '../../components/fieldStyles';
import { subHeading } from '../../components/pageStyles';
import { tableBody, tableCard, tableHead, tableRow, td, th } from '../../components/tableStyles';
import { formatDateTime } from '../../utils/format';

const MAX_NAME_LENGTH = 64;

function RevealDialog({ token, onClose }: { token: string; onClose: () => void }) {
  const titleId = useId();
  return (
    <Modal
      onClose={onClose}
      ariaLabelledBy={titleId}
      panelClassName="max-w-lg w-full"
      header={<h2 id={titleId} className="text-lg font-semibold text-foreground">Copy your API token</h2>}
      footer={<button type="button" onClick={onClose} className={`${dialogFooterBtn} ${btnPrimary}`}>Done</button>}
    >
      <div className="p-4 space-y-3 text-sm text-foreground">
        <p className="font-medium text-warning">This token will not be shown again. Copy it now.</p>
        <p className="text-muted-foreground">
          Treat it like your password: it can read your configuration, including provider keys in an export.
        </p>
        <div className="flex gap-2">
          <input
            readOnly
            value={token}
            aria-label="New API token"
            onFocus={(e) => e.currentTarget.select()}
            className="h-auto min-w-0 flex-1 rounded border border-input bg-background px-2 py-1 font-mono text-xs"
          />
          <CopyButton text={token} label="Copy token" className={`shrink-0 px-3 py-1 ${btnOutline}`} />
        </div>
      </div>
    </Modal>
  );
}

function ApiTokensBlock({ isPasswordSet }: { isPasswordSet: boolean }) {
  const queryClient = useQueryClient();
  const nameId = useId();
  const [name, setName] = useState('');
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Plaintext lives here only; a mutation would keep it in the query cache.
  const [revealed, setRevealed] = useState<string | null>(null);
  const [toRevoke, setToRevoke] = useState<ApiToken | null>(null);
  const creatingRef = useRef(false);

  const { data: tokens = [], isError } = useQuery({
    queryKey: API_TOKENS_QUERY_KEY,
    queryFn: listApiTokens,
    enabled: isPasswordSet,
  });

  const revokeMutation = useMutation({
    mutationFn: (id: number) => revokeApiToken(id),
    onSuccess: () => {
      setToRevoke(null);
      queryClient.invalidateQueries({ queryKey: API_TOKENS_QUERY_KEY });
    },
    onError: (e) => {
      setToRevoke(null);
      setError(getErrorMessage(e, 'Could not revoke the token.'));
    },
  });

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || creatingRef.current) return;
    creatingRef.current = true;
    setCreating(true);
    setError(null);
    try {
      const created = await createApiToken(trimmed);
      setRevealed(created.token);
      setName('');
      queryClient.invalidateQueries({ queryKey: API_TOKENS_QUERY_KEY });
    } catch (err) {
      setError(getErrorMessage(err, 'Could not create the token.'));
    } finally {
      creatingRef.current = false;
      setCreating(false);
    }
  };

  const revokeButton = (token: ApiToken) => (
    <DemoDisabled>
      <button
        type="button"
        onClick={() => setToRevoke(token)}
        aria-label={`Revoke ${token.name}`}
        className={`${btnBase} ${btnDestructiveOutline} shrink-0`}
      >
        Revoke
      </button>
    </DemoDisabled>
  );

  return (
    <div className="mt-6 pt-6 border-t border-border">
      <h3 className={`${subHeading} mb-1`}>API tokens</h3>
      {!isPasswordSet ? (
        <p className="text-sm text-muted-foreground">
          API tokens need an application password. Set one above to create tokens.
        </p>
      ) : (
        <>
          <p className="text-sm text-muted-foreground mb-4">
            Long-lived credentials for scripts and integrations. Send one as <code className="font-mono">Authorization: Bearer</code>.
            A token cannot manage tokens, the password or sessions, or import configuration.
          </p>

          <div className="mb-4 space-y-2">
            {isError ? (
              <p role="alert" className="text-sm text-destructive">Could not load API tokens.</p>
            ) : tokens.length === 0 ? (
              <p className="text-sm text-muted-foreground">No API tokens yet.</p>
            ) : (
              <>
                <div className={`hidden sm:block ${tableCard}`}>
                  <table className="w-full">
                    <thead className={tableHead}>
                      <tr>
                        <th className={th}>Name</th>
                        <th className={th}>Token</th>
                        <th className={th}>Created</th>
                        <th className={th}>Last used</th>
                        <th className={th}><span className="sr-only">Actions</span></th>
                      </tr>
                    </thead>
                    <tbody className={tableBody}>
                      {tokens.map((token) => (
                        <tr key={token.id} className={tableRow}>
                          <td className={`${td} min-w-0 break-all text-foreground`}>{token.name}</td>
                          <td className={`${td} font-mono whitespace-nowrap`}>mp_{token.prefix}...</td>
                          <td className={`${td} whitespace-nowrap`}>{formatDateTime(token.createdAt)}</td>
                          <td className={`${td} whitespace-nowrap`}>{token.lastUsedAt ? formatDateTime(token.lastUsedAt) : 'Never'}</td>
                          <td className={`${td} text-right whitespace-nowrap`}>{revokeButton(token)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="sm:hidden space-y-2">
                  {tokens.map((token) => (
                    <div key={token.id} className="bg-muted/40 rounded-lg border border-border p-4 flex items-center justify-between gap-3">
                      <div className="min-w-0 text-sm">
                        <p className="break-all font-medium text-foreground">{token.name}</p>
                        <p className="font-mono text-xs text-muted-foreground">mp_{token.prefix}...</p>
                        <p className="text-xs text-muted-foreground">
                          Created {formatDateTime(token.createdAt)}. Last used {token.lastUsedAt ? formatDateTime(token.lastUsedAt) : 'never'}.
                        </p>
                      </div>
                      {revokeButton(token)}
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>

          <form onSubmit={create} className="flex flex-wrap items-end gap-2">
            <div className="min-w-0 flex-1 basis-48">
              <label htmlFor={nameId} className={labelBase}>Token name</label>
              <input
                id={nameId}
                type="text"
                value={name}
                maxLength={MAX_NAME_LENGTH}
                onChange={(e) => setName(e.target.value)}
                placeholder="home-assistant"
                className={`${inputBase} w-full`}
              />
            </div>
            <DemoDisabled>
              <button
                type="submit"
                disabled={creating || !name.trim()}
                className={`${btnBase} ${btnPrimary}`}
              >
                {creating ? 'Creating...' : 'Create token'}
              </button>
            </DemoDisabled>
          </form>
          {error && <p role="alert" className="mt-2 text-sm text-destructive">{error}</p>}
        </>
      )}

      {revealed && <RevealDialog token={revealed} onClose={() => setRevealed(null)} />}
      {toRevoke && (
        <ConfirmModal
          title="Revoke API token?"
          confirmLabel="Revoke token"
          busyLabel="Revoking..."
          pending={revokeMutation.isPending}
          onCancel={() => setToRevoke(null)}
          onConfirm={() => revokeMutation.mutate(toRevoke.id)}
        >
          <p>Anything using {toRevoke.name} stops working immediately. This cannot be undone.</p>
        </ConfirmModal>
      )}
    </div>
  );
}

export default ApiTokensBlock;
