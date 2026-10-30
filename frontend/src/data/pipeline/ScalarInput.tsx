import { useEffect, useState } from "react";
import type { Column } from "../../api/types";
import { Input } from "../../components/ui/input";
import { formatScalar, parseList, parseScalar, type Scalar } from "./predicates";

type Props = {
  id?: string;
  "aria-describedby"?: string;
  column?: Column;
} & (
  | { list?: false; value: Scalar; onChange: (value: Scalar) => void }
  | { list: true; value: Scalar[]; onChange: (value: Scalar[]) => void }
);

const sameValue = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/**
 * Text field for a JSON scalar (or a comma-separated list of them). The text the user types is
 * kept as typed ("New York ", "1.", "a,") while the parsed value is pushed up on every change; the
 * display is only reset from the value when it changes from outside (undo, reload).
 */
export function ScalarInput(props: Props) {
  const { column, id } = props;
  const parse = (text: string) =>
    props.list ? parseList(text, column) : parseScalar(text, column);
  const display = (value: Scalar | Scalar[]) =>
    Array.isArray(value) ? value.map(formatScalar).join(", ") : formatScalar(value);

  const [text, setText] = useState(() => display(props.value));
  const value = props.value;
  // biome-ignore lint/correctness/useExhaustiveDependencies: `parse` only closes over props already listed.
  useEffect(() => {
    setText((current) => (sameValue(parse(current), value) ? current : display(value)));
  }, [value]);

  return (
    <Input
      id={id}
      aria-describedby={props["aria-describedby"]}
      value={text}
      onChange={(event) => {
        const next = event.target.value;
        setText(next);
        if (props.list) props.onChange(parseList(next, column));
        else props.onChange(parseScalar(next, column));
      }}
    />
  );
}
