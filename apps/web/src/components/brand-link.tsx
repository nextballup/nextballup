import Image from "next/image";
import Link from "next/link";

type BrandLinkProps = {
  href?: string;
  size?: "sm" | "md";
  priority?: boolean;
  className?: string;
};

const SIZE_CLASSES: Record<NonNullable<BrandLinkProps["size"]>, string> = {
  sm: "h-8 w-8",
  md: "h-10 w-10",
};

export function BrandLink({
  href = "/",
  size = "sm",
  priority = false,
  className = "",
}: BrandLinkProps) {
  return (
    <Link
      href={href}
      className={`inline-flex items-center gap-3 font-semibold tracking-tight ${className}`.trim()}
    >
      <span
        className={`relative overflow-hidden rounded-md ${SIZE_CLASSES[size]}`}
        aria-hidden="true"
      >
        <Image
          src="/brand/logo-color-transparent.png"
          alt=""
          fill
          priority={priority}
          sizes="(max-width: 640px) 32px, 40px"
          className="object-contain"
        />
      </span>
      <span className="text-lg">NextBallUp</span>
    </Link>
  );
}
