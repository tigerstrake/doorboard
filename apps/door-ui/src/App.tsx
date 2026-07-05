import type { DoorboardEvent } from "@doorboard/contracts";

type AppProps = {
  latestEvent?: DoorboardEvent | null;
};

export function App({ latestEvent = null }: AppProps) {
  void latestEvent;
  return null;
}
