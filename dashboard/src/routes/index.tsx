import { createFileRoute } from "@tanstack/react-router";
import { OpsApp } from "@/components/dashboard/ops-app";

export const Route = createFileRoute("/")({ component: Home });

function Home() {
  return <OpsApp />;
}
