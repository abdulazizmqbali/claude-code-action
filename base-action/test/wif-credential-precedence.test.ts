#!/usr/bin/env bun

import { afterEach, beforeEach, describe, expect, spyOn, test } from "bun:test";
import * as core from "@actions/core";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { setupWorkloadIdentity } from "../src/workload-identity";

describe("WIF static credential precedence", () => {
  let originalEnv: NodeJS.ProcessEnv;
  let tempDir: string;
  let getIDTokenSpy: ReturnType<typeof spyOn>;
  let warningSpy: ReturnType<typeof spyOn>;
  let setSecretSpy: ReturnType<typeof spyOn>;

  beforeEach(() => {
    originalEnv = { ...process.env };
    tempDir = mkdtempSync(join(tmpdir(), "wif-precedence-test-"));
    process.env.RUNNER_TEMP = tempDir;
    process.env.ANTHROPIC_FEDERATION_RULE_ID = "fdrl_test";
    process.env.ANTHROPIC_ORGANIZATION_ID =
      "00000000-0000-0000-0000-000000000000";

    delete process.env.ANTHROPIC_API_KEY;
    delete process.env.ANTHROPIC_AUTH_TOKEN;
    delete process.env.CLAUDE_CODE_OAUTH_TOKEN;
    delete process.env.ANTHROPIC_IDENTITY_TOKEN_FILE;
    delete process.env.ANTHROPIC_CONFIG_DIR;
    delete process.env.ANTHROPIC_PROFILE;

    getIDTokenSpy = spyOn(core, "getIDToken").mockResolvedValue(
      "test-identity-token",
    );
    warningSpy = spyOn(core, "warning").mockImplementation(() => {});
    setSecretSpy = spyOn(core, "setSecret").mockImplementation(() => {});
  });

  afterEach(() => {
    process.env = originalEnv;
    getIDTokenSpy.mockRestore();
    warningSpy.mockRestore();
    setSecretSpy.mockRestore();
    rmSync(tempDir, { recursive: true, force: true });
  });

  test("removes empty static credential placeholders before federation", async () => {
    process.env.ANTHROPIC_API_KEY = "";
    process.env.ANTHROPIC_AUTH_TOKEN = "   ";
    process.env.CLAUDE_CODE_OAUTH_TOKEN = "";

    const handle = await setupWorkloadIdentity();
    try {
      expect(handle).toBeDefined();
      expect(process.env.ANTHROPIC_API_KEY).toBeUndefined();
      expect(process.env.ANTHROPIC_AUTH_TOKEN).toBeUndefined();
      expect(process.env.CLAUDE_CODE_OAUTH_TOKEN).toBeUndefined();
      expect(getIDTokenSpy).toHaveBeenCalledWith("https://api.anthropic.com");
    } finally {
      handle?.stop();
    }
  });

  for (const credentialName of [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
  ] as const) {
    test(`preserves non-empty ${credentialName} precedence`, async () => {
      process.env[credentialName] = "real-static-credential";

      const handle = await setupWorkloadIdentity();

      expect(handle).toBeUndefined();
      expect(process.env[credentialName]).toBe("real-static-credential");
      expect(getIDTokenSpy).not.toHaveBeenCalled();
      expect(warningSpy).toHaveBeenCalled();
    });
  }
});
