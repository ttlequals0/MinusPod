import { useEffect, useId, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { API_TOKENS_QUERY_KEY, ApiToken, createApiToken, listApiTokens, revokeAllApiTokens, revokeApiToken } from '../../api/apiTokens';
import { getErrorMessage } from '../../api/client';
import CopyButton from '../../components/CopyButton';
import { DemoDisabled } from '../../components/DemoDisabled';
import DropdownMenu from '../../components/DropdownMenu';
import { ConfirmModal, Modal, dialogFooterBtn } from '../../components/Modal';
import { btnBase, btnDestructiveOutline, btnOutline, btnPrimary } from '../../components/buttonStyles';
import { inputBase, labelBase } from '../../components/fieldStyles';
import { cardTitle, subHeading } from '../../components/pageStyles';
import { tableBody, tableCard } from '../../components/tableStyles';
import { DEMO_WRITE_TOOLTIP } from '../../demo/copy';
import { IS_DEMO } from '../../demo/flag';
import { formatDate, formatDateTime } from '../../utils/format';

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
        <p className="text-muted-foreground">
          Send it as <code className="font-mono">Authorization: Bearer &lt;token&gt;</code>.
        </p>
      </div>
    </Modal>
  );
}

function TokenDetailsModal({ token, onClose }: { token: ApiToken; onClose: () => void }) {
  return (
    <Modal
      ariaLabel="API token details"
      onClose={onClose}
      closeOnBackdrop
      panelClassName="max-w-lg w-full text-left"
      header={<h2 className={`${cardTitle} min-w-0 truncate`} title={token.name}>{token.name}</h2>}
      footer={<button type="button" onClick={onClose} className={`${dialogFooterBtn} ${btnOutline}`}>Close</button>}
    >
      <dl className="p-4 grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-sm">
        <dt className="text-muted-foreground">Name</dt>
        <dd className="min-w-0 break-all text-foreground">{token.name}</dd>
        <dt className="text-muted-foreground">Token</dt>
        <dd className="font-mono text-foreground">mp_{token.prefix}...</dd>
        <dt className="text-muted-foreground">Created</dt>
        <dd className="text-foreground">{formatDateTime(token.createdAt)}</dd>
        <dt className="text-muted-foreground">Last used</dt>
        <dd className="text-foreground">{token.lastUsedAt ? formatDateTime(token.lastUsedAt) : 'Never'}</dd>
      </dl>
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
  const [viewing, setViewing] = useState<ApiToken | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const [focusCreate, setFocusCreate] = useState(0);
  // The revoked row's trigger is gone; move focus to the create input once the dialog has released the page.
  useEffect(() => {
    if (focusCreate === 0) return;
    // The dialog restores focus in a microtask; run after it.
    const timer = setTimeout(() => nameRef.current?.focus(), 0);
    return () => clearTimeout(timer);
  }, [focusCreate]);
  const [confirmRevokeAll, setConfirmRevokeAll] = useState(false);
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
      setFocusCreate((n) => n + 1);
      queryClient.invalidateQueries({ queryKey: API_TOKENS_QUERY_KEY });
    },
    onError: (e) => {
      setToRevoke(null);
      setError(getErrorMessage(e, 'Could not revoke the token.'));
    },
  });

  const revokeAllMutation = useMutation({
    mutationFn: revokeAllApiTokens,
    onSuccess: () => {
      setConfirmRevokeAll(false);
      setFocusCreate((n) => n + 1);
      queryClient.invalidateQueries({ queryKey: API_TOKENS_QUERY_KEY });
    },
    onError: (e) => {
      setConfirmRevokeAll(false);
      setError(getErrorMessage(e, 'Could not revoke the tokens.'));
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

  return (
    <div className="mt-6 pt-6 border-t border-border">
      <h3 className={`${subHeading} mb-1`}>API tokens</h3>
      {!isPasswordSet ? (
        <p className="text-sm text-muted-foreground">
          API tokens need an application password. Set one above to create tokens.
        </p>
      ) : (
        <>
          <p className="text-sm text-muted-foreground mb-4">Tokens let scripts call the API.</p>

          <div className="mb-4 space-y-2">
            {isError ? (
              <p role="alert" className="text-sm text-destructive">Could not load API tokens.</p>
            ) : tokens.length === 0 ? (
              <p className="text-sm text-muted-foreground">No API tokens yet.</p>
            ) : (
              <>
                <div className="flex items-center justify-between gap-3">
                  <p className="text-sm text-muted-foreground">{tokens.length} {tokens.length === 1 ? 'token' : 'tokens'}</p>
                  {tokens.length >= 2 && (
                    <DemoDisabled>
                      <button
                        type="button"
                        onClick={() => setConfirmRevokeAll(true)}
                        className={`${btnBase} ${btnDestructiveOutline}`}
                      >
                        Revoke all
                      </button>
                    </DemoDisabled>
                  )}
                </div>
                <ul className={`${tableCard} ${tableBody}`}>
                  {tokens.map((token) => (
                    <li key={token.id} className="flex items-center justify-between gap-3 px-4 py-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-foreground" title={token.name}>{token.name}</p>
                        <p className="text-xs text-muted-foreground">
                          {token.lastUsedAt ? `Used ${formatDate(token.lastUsedAt)}` : 'Never used'}
                        </p>
                      </div>
                      <div className="shrink-0">
                        <DropdownMenu
                          triggerLabel="Actions"
                          ariaLabel={`Actions for ${token.name}`}
                          triggerClassName={`${btnBase} ${btnOutline}`}
                          chevronClassName="w-3.5 h-3.5"
                          align="right"
                          items={[
                            { title: 'View details', onClick: () => setViewing(token) },
                            {
                              title: 'Revoke',
                              destructive: true,
                              onClick: () => setToRevoke(token),
                              disabled: IS_DEMO,
                              tooltip: IS_DEMO ? DEMO_WRITE_TOOLTIP : undefined,
                            },
                          ]}
                        />
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>

          <form onSubmit={create} className="flex flex-wrap items-end gap-2">
            <div className="min-w-0 flex-1 basis-48">
              <label htmlFor={nameId} className={labelBase}>Token name</label>
              <input
                id={nameId}
                ref={nameRef}
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

      {viewing && <TokenDetailsModal token={viewing} onClose={() => setViewing(null)} />}
      {revealed && <RevealDialog token={revealed} onClose={() => setRevealed(null)} />}
      {confirmRevokeAll && (
        <ConfirmModal
          title="Revoke all API tokens?"
          confirmLabel="Revoke all tokens"
          busyLabel="Revoking..."
          pending={revokeAllMutation.isPending}
          onCancel={() => setConfirmRevokeAll(false)}
          onConfirm={() => revokeAllMutation.mutate()}
        >
          <p>Every integration using a token stops working immediately. This cannot be undone.</p>
        </ConfirmModal>
      )}
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
