# Upload from CI

How to send each CI build to Bingarde from GitHub Actions, GitLab CI and Jenkins. All three use
[`scripts/bingarde-upload.py`](../scripts/bingarde-upload.py): it needs only Python 3, creates
the build, streams the files and sets the final status in one step. The API it uses is in
[api.md](api.md).

## Before you start

1. Sign in to Bingarde with an account that may write those builds (a group with a
   `write` rule that finds them, see [access.md](access.md)) and create a **`write`** token on the **API
   tokens** page.
2. Store it as a secret of the CI system, named `BINGARDE_TOKEN` below. Never put it into
   the pipeline file.
3. The CI runner must reach Bingarde over HTTPS and have `python3`.

The examples fetch the script from a release tag, so a pipeline does not change when the
script does. Copy it into your repo instead if the runners have no internet access.

Every example does the same:

- **Success:** upload the build outputs, status `complete`.
- **Failure:** upload only the log, status `failed`, so the broken build is listed with its
  log. Files that a failed build did not make cannot be uploaded: the script stops on a
  missing file before it creates the build.

The same build number uploaded again (a rerun) updates the existing build instead of making
a new one, and files already stored with the same name and checksum are skipped.

## GitHub Actions

Add the token under **Settings → Secrets and variables → Actions** as `BINGARDE_TOKEN`, and
the server address as the variable `BINGARDE_URL`.

```yaml
jobs:
  build-windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build
        run: ./build.cmd > build.log 2>&1

      - name: Upload to Bingarde
        if: always()                 # also after a failed build step
        shell: bash
        env:
          BINGARDE_URL: ${{ vars.BINGARDE_URL }}
          BINGARDE_TOKEN: ${{ secrets.BINGARDE_TOKEN }}
          JOB_STATUS: ${{ job.status }}
        run: |
          curl -fsSLO https://raw.githubusercontent.com/vasiliok/Bingarde/v1.1.0/scripts/bingarde-upload.py
          if [ "$JOB_STATUS" = success ]; then
            set -- --status complete out/Game.exe out/Game.pdb build.log
          else
            set -- --status failed build.log
          fi
          python bingarde-upload.py \
            --project game --branch "$GITHUB_REF_NAME" --platform windows \
            --config release --arch x86_64 \
            --build "$GITHUB_RUN_NUMBER" --revision "$GITHUB_SHA" \
            --ci-url "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID" \
            "$@"
```

With a build matrix, pass the matrix values: `--platform ${{ matrix.os }}`,
`--arch ${{ matrix.arch }}`. Every combination becomes its own build in Bingarde.

## GitLab CI

Add `BINGARDE_TOKEN` (masked) and `BINGARDE_URL` under **Settings → CI/CD → Variables**.
`after_script` runs after a failed `script` too, and `CI_JOB_STATUS` tells which one it was.

```yaml
build-linux:
  image: gcc:14
  script:
    - make > build.log 2>&1
  after_script:
    - curl -fsSLO https://raw.githubusercontent.com/vasiliok/Bingarde/v1.1.0/scripts/bingarde-upload.py
    - |
      if [ "$CI_JOB_STATUS" = success ]; then
        set -- --status complete out/game out/game.debug build.log
      else
        set -- --status failed build.log
      fi
      python3 bingarde-upload.py \
        --project game --branch "$CI_COMMIT_REF_NAME" --platform linux \
        --config release --arch x86_64 \
        --build "$CI_PIPELINE_IID" --revision "$CI_COMMIT_SHA" --ci-url "$CI_JOB_URL" \
        "$@"
```

A failing command in `after_script` does not fail the job. Look at the job log to see
whether the upload worked.

## Jenkins

Add the token as a **Secret text** credential with the ID `bingarde-token`. This example is
a declarative pipeline on a Linux agent; on Windows agents use `bat` and `python`.

```groovy
pipeline {
  agent { label 'linux' }
  environment {
    BINGARDE_URL = 'https://bingarde.example.com'
  }
  stages {
    stage('Build') {
      steps {
        sh 'make > build.log 2>&1'
      }
    }
  }
  post {
    always {
      withCredentials([string(credentialsId: 'bingarde-token', variable: 'BINGARDE_TOKEN')]) {
        withEnv(["RESULT=${currentBuild.currentResult}"]) {
          sh '''
          curl -fsSLO https://raw.githubusercontent.com/vasiliok/Bingarde/v1.1.0/scripts/bingarde-upload.py
          if [ "$RESULT" = SUCCESS ]; then
            set -- --status complete out/game out/game.debug build.log
          else
            set -- --status failed build.log
          fi
          python3 bingarde-upload.py \
            --project game --branch "${BRANCH_NAME:-main}" --platform linux \
            --config release --arch x86_64 \
            --build "$BUILD_NUMBER" --revision "$GIT_COMMIT" --ci-url "$BUILD_URL" \
            "$@"
          '''
        }
      }
    }
  }
}
```

`BRANCH_NAME` is set only in multibranch pipelines; elsewhere give the branch by hand.

## More

- `--property KEY=VALUE` (repeatable) stores extra facts with the build, for example the
  compiler version: `--property compiler="gcc 14.2"`.
- `--role symbols` sets the role of all files of one call; by default it comes from the file
  name (see `bingarde/config/filetypes.json`).
- Without Python on the runner, the same three requests with `curl` are in the
  [README](../README.md#upload-from-ci).
