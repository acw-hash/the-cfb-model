import type { Metadata } from "next";
import ModelWalkthrough from "../../components/ModelWalkthrough/ModelWalkthrough";

export const metadata: Metadata = {
  title: "Visual",
  description:
    "An interactive walkthrough of how Ridge turns college football data into a forecast with uncertainty.",
};

export default function VisualPage() {
  return <ModelWalkthrough />;
}
