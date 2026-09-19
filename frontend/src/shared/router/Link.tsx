import type { AnchorHTMLAttributes, MouseEvent } from "react";
import { useRouter } from "./context";
import { routeToPath, type Route } from "./routes";

interface LinkProps extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> {
  to: Route | string;
}

/**
 * Âncora real (`href` funcional: abrir em nova aba, copiar endereço e teclado
 * funcionam) que navega sem recarregar em clique simples.
 */
export function Link({ to, onClick, children, ...rest }: LinkProps) {
  const { navigate } = useRouter();
  const href = typeof to === "string" ? to : routeToPath(to);

  function handleClick(event: MouseEvent<HTMLAnchorElement>) {
    onClick?.(event);
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      (rest.target && rest.target !== "_self")
    ) {
      return;
    }
    event.preventDefault();
    navigate(href);
  }

  return (
    <a {...rest} href={href} onClick={handleClick}>
      {children}
    </a>
  );
}
