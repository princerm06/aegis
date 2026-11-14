import type { Metadata } from "next";
import { CommandCenter } from "@/components/CommandCenter";

export const metadata: Metadata = {
  title: "AEGIS Command Center",
  description: "Human-supervised emergency coordination dashboard (simulation).",
};

export default function CommandPage() {
  return <CommandCenter />;
}
