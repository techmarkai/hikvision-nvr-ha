# Getting the integration icon into Home Assistant

Home Assistant does not read integration icons from the integration itself. It
fetches them from `brands.home-assistant.io` by domain, and that content comes
from the [home-assistant/brands](https://github.com/home-assistant/brands)
repository. There is no local override — until the domain exists there, the
integration page shows the default placeholder no matter what this repo ships.

Custom integrations *are* accepted: they live under `custom_integrations/`,
alongside a thousand others.

## Why this is not already submitted

The brands repository's `AI_POLICY.md` says:

> **We do not allow autonomous agents to be used for contributing to our
> projects.** We will close any pull requests or issues that we believe were
> created autonomously.

So the pull request has to come from you, as a person who has looked at the
images and can answer questions about them. The files are ready below.

## Submitting

The assets in `custom_integrations/hikvision_nvr/` already match the required
sizes (`icon.png` 256×256, `icon@2x.png` 512×512, `logo.png`, `logo@2x.png`,
transparent PNG).

```bash
gh repo fork home-assistant/brands --clone --remote
cd brands
git checkout -b hikvision_nvr
mkdir -p custom_integrations/hikvision_nvr
cp /path/to/this/repo/brand/home-assistant-brands/custom_integrations/hikvision_nvr/*.png \
   custom_integrations/hikvision_nvr/
python3 -m pip install -r requirements.txt   # the repo ships an image checker
python3 -m script.validate                   # confirm sizes and transparency
git add custom_integrations/hikvision_nvr
git commit -m "Add Hikvision NVR custom integration brand"
git push -u origin hikvision_nvr
gh pr create --fill
```

Their PR template asks which domain you are adding and whether you own the
artwork. The answer to the second is yes: `brand/icon.svg` in this repository is
original work, not the Hikvision logo — that is Hikvision's trademark, and
submitting it would be someone else's asset to license.

Once merged, the icon appears on the integration page automatically. Nothing in
this repository needs to change.
