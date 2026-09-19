import { describeError, messageForCode } from "../api/messages";
import { isApiError } from "../api/errors";

interface ErrorNoticeProps {
  /** Erro lançado por uma chamada da API (vira texto pela tabela única). */
  error?: unknown;
  /** Ou um código de erro registrado (errorCode de job/preparação). */
  code?: string | null;
  title?: string;
  onRetry?: () => void;
  retryLabel?: string;
}

/** Mensagem de erro acessível (role="alert"): texto da tabela única, nunca corpo remoto. */
export function ErrorNotice({ error, code, title, onRetry, retryLabel }: ErrorNoticeProps) {
  const text = code !== undefined ? messageForCode(code) : describeError(error);
  const requestId = isApiError(error) ? error.requestId : null;
  return (
    <div className="notice notice--error" role="alert">
      {title ? <p className="notice__title">{title}</p> : null}
      <p>{text}</p>
      {requestId ? <p className="notice__meta">ID da requisição: {requestId}</p> : null}
      {onRetry ? (
        <button type="button" className="button button--secondary" onClick={onRetry}>
          {retryLabel ?? "Tentar novamente"}
        </button>
      ) : null}
    </div>
  );
}
