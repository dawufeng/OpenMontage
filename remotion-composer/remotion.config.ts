import { Config } from "@remotion/cli/config";

Config.setChromiumOpenGlRenderer("swangle");
Config.setConcurrency(1);
Config.setBrowserExecutable("/usr/bin/google-chrome");
