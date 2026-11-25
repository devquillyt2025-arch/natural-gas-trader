import { Skeleton } from "@/components/ui";

export default function Loading() {
  return (
    <div className="glass">
      <Skeleton lines={10} />
    </div>
  );
}
