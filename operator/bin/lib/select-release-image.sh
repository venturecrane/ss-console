#!/usr/bin/env bash
# select-release-image.sh: which image provision-customer.sh deploys (2026-10-21).
#
# Sourced. Defines select_release_image <bin-dir> <slug>, which prints the image
# on stdout and says why on stderr, or returns non-zero having said why.
#
# Reprovisioning re-applies ONE seat's config and secrets; it never moves that
# seat's software (that is `fleet release`). So:
#
#   fleet seat-image exit 0  an existing seat: redeploy the image it runs now.
#                            No other seat, and no release's completion, can
#                            block it or downgrade it.
#   fleet seat-image exit 1  a new seat (no Machine yet): the newest release
#                            that completed its last ring (fleet latest-image).
#   any other exit           Fly could not tell. Refuse: "could not tell" must
#                            never read as "new seat".
#
# Before 2026-10-21 every reprovision deployed latest-image, so one seat's
# failed rollout (no release could complete) refused every seat's reprovision
# for a day. Tests: operator/bin/tests/test_provision_image_selection.py.

select_release_image() {
  local bin="$1" slug="$2" image="" rc=0
  image="$("${bin}/fleet" seat-image "${slug}")" || rc=$?
  if [ "${rc}" = 0 ]; then
    echo "select-release-image: hermes-${slug} is redeployed on the image it runs now, ${image}" >&2
  elif [ "${rc}" = 1 ]; then
    if ! image="$("${bin}/fleet" latest-image)"; then
      echo "select-release-image: new seat, and no fleet release has completed its last ring (operator/bin/fleet releases). Run \`operator/bin/fleet build\` and \`fleet release\`, or pass --build to build this checkout" >&2
      return 1
    fi
    echo "select-release-image: new seat: hermes-${slug} gets release image ${image}" >&2
  else
    echo "select-release-image: could not read the image hermes-${slug} runs now (fleet seat-image exit ${rc}); refusing to guess" >&2
    return 1
  fi
  printf '%s\n' "${image}"
}
