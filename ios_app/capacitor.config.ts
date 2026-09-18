import type { CapacitorConfig } from "@capacitor/cli";

const configuredUrl = process.env.OPENMIND_APP_URL?.trim();

const config: CapacitorConfig = {
    appId: "com.openmind.app",
    appName: "OpenMind",
    webDir: "h5/dist",
    loggingBehavior: "production",
};

if (configuredUrl) {
    let parsedUrl: URL;

    try {
        parsedUrl = new URL(configuredUrl);
    } catch {
        throw new Error(`OPENMIND_APP_URL is not a valid URL: ${configuredUrl}`);
    }

    config.server = {
        url: parsedUrl.toString(),
        cleartext: parsedUrl.protocol === "http:",
        allowNavigation: [parsedUrl.hostname],
    };
}

export default config;
