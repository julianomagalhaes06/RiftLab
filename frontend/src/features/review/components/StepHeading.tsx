import { useEffect, useRef } from "react";

interface StepHeadingProps {
  stepKey: string;
  id: string;
  children: React.ReactNode;
}

/**
 * Move o foco para o titulo de cada nova etapa (sem armadilha de foco), para que
 * leitores de tela e navegacao por teclado acompanhem a troca de tela. O id fica
 * no proprio h2 (elemento focado), nao em um filho, para que aria-labelledby e
 * verificacoes de foco apontem para o mesmo elemento.
 */
export function StepHeading({ stepKey, id, children }: StepHeadingProps) {
  const ref = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    ref.current?.focus();
  }, [stepKey]);

  return (
    <h2 ref={ref} id={id} tabIndex={-1} className="step-heading">
      {children}
    </h2>
  );
}
