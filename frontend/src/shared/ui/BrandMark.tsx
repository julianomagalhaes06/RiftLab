/** Marca do RiftLab: o cristal fendido, escolhido pelo usuário em 27/11/2026 (opção 07 de
 * `.private/marca/logo-opcoes-2026-11-27/`). Decorativa: o nome "RiftLab" vem ao lado, em texto.
 * O mesmo desenho está em `public/favicon.svg`; cores nos tokens `--brand-*`. */
export function BrandMark() {
  return (
    <svg className="brand-mark" viewBox="14 2 36 60" aria-hidden="true" focusable="false">
      <polygon className="brand-mark__deep" points="32,3 15,25 32,29" />
      <polygon className="brand-mark__light" points="32,3 49,25 32,29" />
      <polygon className="brand-mark__base" points="15,25 32,61 32,29" />
      <polygon className="brand-mark__mid" points="49,25 32,61 32,29" />
      <path className="brand-mark__seam" d="M18 33L26 30 32 36 39 31 46 33" />
    </svg>
  );
}
