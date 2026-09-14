interface QualityNoticeProps {
  tone: "info" | "warning" | "unavailable";
  label: string;
  children: React.ReactNode;
}

const TONE_PREFIX: Record<QualityNoticeProps["tone"], string> = {
  info: "Informação:",
  warning: "Atenção:",
  unavailable: "Indisponível:",
};

/**
 * Estado nunca depende só de cor: cada aviso carrega um prefixo textual fixo
 * além da classe de estilo.
 */
export function QualityNotice({ tone, label, children }: QualityNoticeProps) {
  return (
    <div className={`quality-notice quality-notice--${tone}`} role="status">
      <p className="quality-notice__label">
        {TONE_PREFIX[tone]} {label}
      </p>
      <p className="quality-notice__body">{children}</p>
    </div>
  );
}
